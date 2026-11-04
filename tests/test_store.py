"""The store, against every dialect it claims to support.

Runs on SQLite always. Runs on MySQL too when TEST_MYSQL_URL is set, so the
suite still passes on a laptop with no server while the dialect that production
uses is genuinely covered rather than assumed:

    TEST_MYSQL_URL=mysql://root:devroot@127.0.0.1:13306/newsai python -m tests.test_store

What is being checked is not that SQL runs. It is that the four statements the
pipeline relies on mean the same thing on both engines, and that the text going
through them comes back byte for byte — because every way this can break is
silent. A collation that folds diacritics does not raise; it merges two places
into one and the clustering looks unusually good.
"""
import os
import sys
import tempfile

from newsai import db
from newsai import store

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def schema_is_idempotent(con):
    """connect() migrates; a second pass has nothing left to do.
    The mechanism itself is covered by test_migrate.py."""
    store.migrate(con)
    added = store.migrate(con)
    return added == [], f"second migrate applied {added}, want []"


@case
def insert_or_replace_overwrites(con):
    for text in ("first", "second"):
        con.execute("INSERT OR REPLACE INTO posts (id, channel, text) VALUES (?,?,?)",
                    ("p-1", "lbci", text))
    rows = con.execute("SELECT text FROM posts WHERE id=?", ("p-1",)).fetchall()
    return (len(rows) == 1 and rows[0]["text"] == "second",
            f"got {[r['text'] for r in rows]}, want ['second']")


@case
def insert_or_ignore_keeps_the_first(con):
    for note in ("original", "clobber"):
        con.execute("INSERT OR IGNORE INTO runs (run_id, started_at, note) VALUES (?,?,?)",
                    ("r-1", "2026-08-31T00:00:00", note))
    r = con.execute("SELECT note FROM runs WHERE run_id=?", ("r-1",)).fetchone()
    return r["note"] == "original", f"got {r['note']!r}, want 'original'"


@case
def upsert_reads_the_excluded_row(con):
    """the live runner's cursor write: the second call must move the cursor."""
    for msg_id in (5, 9):
        con.execute("INSERT INTO channel_cursor (channel, handle, last_msg_id, updated_at)"
                    " VALUES (?,?,?,?) ON CONFLICT(channel) DO UPDATE SET"
                    " handle=excluded.handle, last_msg_id=excluded.last_msg_id,"
                    " updated_at=excluded.updated_at",
                    ("lbci", "@LBCI_NEWS", msg_id, "2026-08-31T00:00:00"))
    r = con.execute("SELECT last_msg_id FROM channel_cursor WHERE channel=?", ("lbci",)).fetchone()
    return r["last_msg_id"] == 9, f"cursor at {r['last_msg_id']}, want 9"


@case
def upsert_can_increment_in_place(con):
    """run.py's place proposal: the counter must add, not reset."""
    for _ in range(3):
        con.execute("INSERT INTO place_proposals (child, parent, run_id, post_id)"
                    " VALUES (?,?,?,?) ON CONFLICT(child, parent) DO UPDATE SET seen = seen + 1",
                    ("شحور", "قضاء صور", "r-1", "p-1"))
    r = con.execute("SELECT seen FROM place_proposals WHERE child=?", ("شحور",)).fetchone()
    return r["seen"] == 3, f"seen={r['seen']}, want 3"


@case
def autoincrement_returns_its_id(con):
    """run.py takes story_id and development_id straight off the cursor."""
    ids = []
    for n in (1, 2):
        cur = con.execute("INSERT INTO stories (run_id, title) VALUES (?,?)", ("r-1", f"t{n}"))
        ids.append(cur.lastrowid)
    return (all(ids) and ids[1] == ids[0] + 1), f"lastrowid {ids}, want two consecutive ids"


@case
def arabic_and_emoji_survive_the_round_trip(con):
    """utf8mb4, not utf8: 111 of 282 live posts carry a 4-byte pictograph, and
    MySQL's 3-byte `utf8` truncates the row at the first one."""
    text = "عاجل 🔴 الجيش يعلن ⏬ انتهاء العملية — 50% و +5 درجات"
    con.execute("INSERT OR REPLACE INTO posts (id, text) VALUES (?,?)", ("p-utf8", text))
    got = con.execute("SELECT text FROM posts WHERE id=?", ("p-utf8",)).fetchone()["text"]
    return got == text, f"got {got!r}"


@case
def diacritics_are_not_folded_away(con):
    """The one that decides whether the port is safe.

    place_proposals keys on the model's raw location strings, unnormalized. Under
    MySQL's default utf8mb4_0900_ai_ci these two differ only by a combining fatha,
    compare equal, and collapse into a single row on insert — so a place the
    corpus never claimed becomes a confirmed parent. utf8mb4_bin keeps them apart.
    """
    plain, marked = "شحور", "شَحور"
    assert plain != marked
    for child in (plain, marked):
        con.execute("INSERT INTO place_proposals (child, parent, run_id, post_id)"
                    " VALUES (?,?,?,?) ON CONFLICT(child, parent) DO UPDATE SET seen = seen + 1",
                    (child, "قضاء صور", "r-1", "p-1"))
    n = len(con.execute("SELECT child FROM place_proposals").fetchall())
    return n == 2, f"{n} row(s) — the two spellings collapsed into one"


@case
def latest_run_tiebreak_survives_rowid_loss(con):
    """report.latest() used SQLite's rowid to break ties on started_at."""
    same = "2026-08-31T12:00:00"
    for rid in ("run-20260831T120000", "run-20260831T115900"):
        con.execute("INSERT OR IGNORE INTO runs (run_id, started_at) VALUES (?,?)", (rid, same))
    r = con.execute("SELECT run_id FROM runs WHERE started_at=?"
                    " ORDER BY started_at DESC, run_id DESC LIMIT 1", (same,)).fetchone()
    return r["run_id"] == "run-20260831T120000", f"picked {r['run_id']}"


@case
def null_stays_null(con):
    """UNKNOWN is a real value here: places.match treats empty as 'no claim',
    never as a wildcard, so an empty string arriving where NULL was written
    would change a match."""
    con.execute("INSERT OR REPLACE INTO posts (id, text, source) VALUES (?,?,?)",
                ("p-null", "x", None))
    r = con.execute("SELECT source FROM posts WHERE id=?", ("p-null",)).fetchone()
    return r["source"] is None, f"got {r['source']!r}, want None"


@case
def prefix_keeps_us_out_of_the_neighbours_tables(con):
    """A shared MySQL database already has a `posts` table, and REPLACE INTO is
    delete+insert — unprefixed, the first run empties it."""
    if not con.prefix:
        return True, ""
    name = con.table("posts")
    if con.dialect == db.MYSQL:
        n = con.execute("SELECT COUNT(*) AS c FROM information_schema.tables"
                        " WHERE table_schema = DATABASE() AND table_name = ?", (name,)
                        ).fetchone()["c"]
    else:
        n = con.execute("SELECT COUNT(*) AS c FROM sqlite_master"
                        " WHERE type='table' AND name = ?", (name,)).fetchone()["c"]
    return n == 1, f"no table called {name}"


def run_against(label, target, reset):
    con = store.connect(target)
    reset(con)
    fails = 0
    for fn in CASES:
        try:
            ok, why = fn(con)
        except Exception as e:
            ok, why = False, f"{type(e).__name__}: {e}"
        con.commit()
        print(f"{'ok  ' if ok else 'FAIL'} {label:7s} {fn.__name__:42s} {'' if ok else why}")
        fails += not ok
    con.close()
    return fails


TABLES = ["posts", "runs", "classifications", "stories", "developments",
          "confirmations", "decisions", "channel_cursor", "place_proposals", "labels",
          "schema_migrations"]


def main():
    fails = 0
    path = tempfile.mktemp(suffix=".db")
    fails += run_against("sqlite", path, lambda c: None)
    os.unlink(path)

    # The same cases again with a prefix, so the rewrite is covered without
    # needing a server. This is the configuration a shared MySQL database uses.
    path = tempfile.mktemp(suffix=".db")
    fails += run_against("prefixed", f"sqlite:///{path}?prefix=ai_aggregator_", lambda c: None)
    os.unlink(path)

    url = os.environ.get("TEST_MYSQL_URL")
    if url:
        def wipe(con):
            for t in TABLES:
                con.execute(f"DELETE FROM {t}")
            con.commit()
        fails += run_against("mysql", url, wipe)
    else:
        print("\nskipped mysql — set TEST_MYSQL_URL to cover the dialect production uses")

    total = len(CASES) * (3 if url else 2)
    print(f"\n{total - fails}/{total} passed")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
