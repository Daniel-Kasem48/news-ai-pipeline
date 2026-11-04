"""One connection surface over SQLite and MySQL.

Why both rather than a straight port: the tests need a database they can create
in a temp file and throw away, and production needs the MySQL the rest of the
estate already runs. Porting outright would leave the test suite needing a
server to say whether `normalize_key` still deduplicates correctly.

Call sites are unchanged. They still write `?` placeholders and read rows by
column name; everything dialect-specific is in this file.

The one thing to understand before editing: on MySQL the collation is
`utf8mb4_bin`, not the server default. The default `utf8mb4_0900_ai_ci` is
accent- and case-insensitive, and this schema's primary keys are Arabic text
(`place_proposals`) and normalized dedup keys. Under a ci collation two places
that differ only in diacritics collide on insert and silently become one row —
a failure that looks like the clustering working unusually well.
"""
import os
import re
import sqlite3
from urllib.parse import parse_qs, unquote, urlparse

SQLITE, MYSQL = "sqlite", "mysql"

# Every table this pipeline owns. The list exists so the names can be prefixed:
# a MySQL database is usually somebody else's too, and `posts`, `runs`, `labels`
# and `stories` are exactly the names an application framework picks. Sharing a
# schema unprefixed is not a naming annoyance — `REPLACE INTO posts` is
# delete+insert, so the first run would quietly empty the other application's
# table.
TABLES = ("runs", "posts", "classifications", "stories", "developments",
          "confirmations", "decisions", "channel_cursor", "place_proposals",
          "labels", "schema_migrations")

# A dedicated SQLite file needs no prefix and existing ones must keep working,
# so the default is empty there. A MySQL database gets one unless the caller
# explicitly asks for none with `?prefix=` — the failure it prevents is silent
# and destructive, so opting out is a decision someone has to write down.
DEFAULT_MYSQL_PREFIX = "ai_aggregator_"

# \b on both sides, so `posts` inside `ix_posts_ts` or inside an already
# prefixed `ai_aggregator_posts` is left alone: underscore is a word character,
# so there is no boundary there and applying a prefix twice is impossible.
_TABLE_RE = re.compile(r"\b(" + "|".join(TABLES) + r")\b")


# --- statement translation --------------------------------------------------

# Rewrites keyed to SQLite's spelling, because that is what the call sites are
# written in. Each has exactly one MySQL equivalent here; anything subtler than
# these belongs in the caller, not in a regex.
_REWRITES = [
    # REPLACE INTO is delete+insert, which would be wrong if these tables had
    # AUTO_INCREMENT keys or foreign-key children. None of the four that use it
    # do: posts, classifications, decisions and labels all have natural primary
    # keys and nothing references them. So the semantics match SQLite's.
    (re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.I), "REPLACE INTO"),
    (re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I), "INSERT IGNORE INTO"),
    # Aliasing the new row `excluded` is what lets `excluded.handle` in the
    # caller's SQL work unchanged on both engines. MySQL's other spelling,
    # VALUES(handle), is deprecated as of 8.0.20.
    (re.compile(r"\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b", re.I),
     "AS excluded ON DUPLICATE KEY UPDATE"),
]


def _split_quotes(sql):
    """Yield (text, is_literal) so rewriting never reaches inside a string."""
    out, buf, quote = [], [], None
    for ch in sql:
        if quote:
            buf.append(ch)
            if ch == quote:
                out.append(("".join(buf), True))
                buf, quote = [], None
        elif ch in ("'", '"'):
            if buf:
                out.append(("".join(buf), False))
            buf, quote = [ch], ch
        else:
            buf.append(ch)
    if buf:
        out.append(("".join(buf), quote is not None))
    return out


def split_statements(script):
    """Split a schema script on statement boundaries.

    Naive `script.split(";")` cuts a table definition in half, because the
    comments in this schema contain prose: "-- the post that created it;
    confirmations add more via `confirmations`". So semicolons are only
    boundaries outside string literals and outside line comments.
    """
    out, buf, quote, comment = [], [], None, False
    for ch in script:
        if comment:
            if ch == "\n":
                comment = False
                buf.append(ch)
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "-" and buf and buf[-1] == "-":
            buf.pop()
            comment = True
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
        else:
            buf.append(ch)
    stmt = "".join(buf).strip()
    if stmt:
        out.append(stmt)
    return out


def translate(sql, dialect, has_params, prefix=""):
    """SQLite-flavoured SQL in, the dialect's flavour out."""
    if dialect == MYSQL:
        for pattern, repl in _REWRITES:
            sql = pattern.sub(repl, sql)
    parts = []
    for chunk, is_literal in _split_quotes(sql):
        if not is_literal and prefix:
            chunk = _TABLE_RE.sub(prefix + r"\1", chunk)
        if dialect == MYSQL:
            # PyMySQL interpolates with %, so every literal % has to be doubled
            # — inside string literals too, where a LIKE pattern would live.
            if has_params:
                chunk = chunk.replace("%", "%%")
            if not is_literal:
                chunk = chunk.replace("?", "%s")
        parts.append(chunk)
    return "".join(parts)


# --- connection -------------------------------------------------------------

class Conn:
    """Thin wrapper. `execute` returns the driver's cursor, which on both
    engines is iterable and supports fetchone/fetchall with rows addressed by
    column name."""

    def __init__(self, raw, dialect, prefix=""):
        self._raw = raw
        self.dialect = dialect
        self.prefix = prefix

    def table(self, name):
        """The real name of one of our tables, for the places a table name has
        to be passed as a value rather than as SQL — information_schema."""
        return f"{self.prefix}{name}"

    def execute(self, sql, params=()):
        sql = translate(sql, self.dialect, bool(params), self.prefix)
        if self.dialect == SQLITE:
            return self._raw.execute(sql, params)
        cur = self._raw.cursor()
        cur.execute(sql, params or None)
        return cur

    def executescript(self, script):
        """Statement-at-a-time: MySQL drivers refuse several statements in one
        execute."""
        for stmt in split_statements(script):
            self.execute(stmt)
        self.commit()

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


def resolve(target=None):
    """Where the state lives.

    A bare path stays a SQLite file, so every existing `--db stream.db` keeps
    working. DATABASE_URL wins when the caller did not name one, which is how
    the container is pointed at MySQL without changing any command line.
    """
    if target and "://" in target:
        return target
    if target:
        return f"sqlite:///{target}"
    return os.environ.get("DATABASE_URL") or "sqlite:///stream.db"


def _prefix(url, default):
    """`?prefix=ai_aggregator_` on the URL, else the dialect's default.

    Present-but-empty (`?prefix=`) means the caller deliberately wants the bare
    names, which on MySQL is the destructive option — so it has to be written
    out rather than reached by leaving something unset.
    """
    q = parse_qs(urlparse(url).query, keep_blank_values=True)
    if "prefix" in q:
        return q["prefix"][0]
    return os.environ.get("DB_TABLE_PREFIX", default)


def connect(target=None):
    url = resolve(target)
    if url.startswith("sqlite:"):
        # The usual convention: sqlite:///relative.db has three slashes and
        # sqlite:////abs/path.db has four, so exactly one leading slash belongs
        # to the URL and the rest belongs to the path.
        rest = url[len("sqlite://"):]
        rest = rest.split("?", 1)[0]      # ?prefix=... is config, not filename
        path = rest[1:] if rest.startswith("/") else rest
        raw = sqlite3.connect(path or ":memory:")
        raw.row_factory = sqlite3.Row
        return Conn(raw, SQLITE, _prefix(url, ""))

    import pymysql
    from pymysql.cursors import DictCursor
    u = urlparse(url)
    prefix = _prefix(url, DEFAULT_MYSQL_PREFIX)
    raw = pymysql.connect(
        host=u.hostname or "127.0.0.1",
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=(u.path or "/").lstrip("/"),
        cursorclass=DictCursor,
        charset="utf8mb4",
        # Matches the schema. Left to the server default this would be
        # utf8mb4_0900_ai_ci and comparisons would stop distinguishing
        # diacritics — see the module docstring.
        collation="utf8mb4_bin",
        autocommit=False,
    )
    return Conn(raw, MYSQL, prefix)
