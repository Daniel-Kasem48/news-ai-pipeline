"""Schema migrations, one numbered pair of files per change.

    migrations/0002_post_timestamps.sqlite.sql
    migrations/0002_post_timestamps.mysql.sql

Applied in numeric order and recorded in `schema_migrations`, so every database
— a laptop's SQLite file, a shared MySQL schema — ends up at the same version by
the same route. `store.connect()` runs anything pending, so there is no deploy
step to remember; this module is also a CLI for looking and for doing it by hand.

    python -m newsai.migrate --status
    python -m newsai.migrate --db mysql://user:pw@host/db

Rules that keep this honest:

  * Both dialects, always. A migration present for one engine and missing for
    the other would apply, record itself as done, and never run on the other —
    so a missing file is an error, not a skip.
  * Applied files are never edited and numbers are never reused. Fix a mistake
    with a new migration; the ledger keys on the id, so an edit reaches only
    databases that had not run it yet, which is the worst of both.
  * No down path. Rolling the image back runs older code against a newer
    schema, so a migration has to leave the previous release working: widen a
    column rather than narrowing it, and never drop one in the same release
    that stops writing it.

The `-- probe: table.column` header is used for one thing only: adopting a
database that predates this ledger. Its column is checked, and if it is already
there the migration is recorded without being run.
"""
import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from newsai import db

DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(sqlite|mysql)\.sql$")
_PROBE = re.compile(r"^--\s*probe:\s*(\w+)\.(\w+)\s*$", re.M)

LEDGER = {
    db.SQLITE: """CREATE TABLE IF NOT EXISTS schema_migrations (
                    id         TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL)""",
    db.MYSQL: """CREATE TABLE IF NOT EXISTS schema_migrations (
                   id         VARCHAR(191) NOT NULL,
                   applied_at VARCHAR(40)  NOT NULL,
                   PRIMARY KEY (id)
                 ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""",
}


class Migration:
    def __init__(self, num, name, paths):
        self.num, self.name, self.paths = num, name, paths
        self.id = f"{num}_{name}"

    def sql(self, dialect):
        return self.paths[dialect].read_text()

    def probe(self, dialect):
        """(table, column) whose presence means this is already applied."""
        m = _PROBE.search(self.sql(dialect))
        return (m.group(1), m.group(2)) if m else None

    def __repr__(self):
        return f"<{self.id}>"


def discover(directory=DIR):
    """Every migration on disk, in order. Raises if one is missing a dialect."""
    found = {}
    for path in sorted(Path(directory).glob("*.sql")):
        m = _NAME.match(path.name)
        if not m:
            raise ValueError(
                f"{path.name}: expected NNNN_lower_snake_name.<sqlite|mysql>.sql")
        num, name, dialect = m.groups()
        found.setdefault((num, name), {})[dialect] = path

    out = []
    for (num, name), paths in sorted(found.items()):
        missing = {db.SQLITE, db.MYSQL} - set(paths)
        if missing:
            raise ValueError(f"{num}_{name}: no {'/'.join(sorted(missing))} file — "
                             "a migration has to exist for both engines")
        out.append(Migration(num, name, paths))

    nums = [m.num for m in out]
    if len(set(nums)) != len(nums):
        raise ValueError(f"duplicate migration number in {sorted(nums)}")
    return out


def _has_column(con, table, column):
    if con.dialect == db.MYSQL:
        rows = con.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns"
            " WHERE table_schema = DATABASE() AND table_name = ? AND COLUMN_NAME = ?",
            (con.table(table), column))
    else:
        rows = [r for r in con.execute(f"PRAGMA table_info({con.table(table)})")
                if r[1] == column]
    return bool(list(rows))


def applied(con):
    con.executescript(LEDGER[con.dialect])
    return {r["id"] for r in con.execute("SELECT id FROM schema_migrations")}


def _record(con, mid):
    con.execute("INSERT INTO schema_migrations (id, applied_at) VALUES (?,?)",
                (mid, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()


def adopt(con, migrations=None):
    """Record, without running, migrations an existing database already has.

    Only for a database built before this ledger existed. It runs once — the
    moment anything is recorded, the ledger is no longer empty and this is a
    no-op forever after.
    """
    migrations = discover() if migrations is None else migrations
    if applied(con):
        return []
    stamped = []
    for m in migrations:
        probe = m.probe(con.dialect)
        if not probe or not _has_column(con, *probe):
            break            # the first gap ends it: later ones must actually run
        _record(con, m.id)
        stamped.append(m.id)
    return stamped


def pending(con, migrations=None):
    migrations = discover() if migrations is None else migrations
    done = applied(con)
    return [m for m in migrations if m.id not in done]


def run(con, migrations=None):
    """Apply everything outstanding. Safe to call on every connect."""
    migrations = discover() if migrations is None else migrations
    adopt(con, migrations)
    ran = []
    # One transaction per migration, not one for all of them: MySQL commits DDL
    # implicitly, so a batch that dies halfway would leave the tables changed
    # and the ledger empty, and the next run would replay statements that had
    # already been applied.
    for m in pending(con, migrations):
        con.executescript(m.sql(con.dialect))
        _record(con, m.id)
        ran.append(m.id)
    return ran


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", "sqlite:///newsai.db"),
                    help="DATABASE_URL, or a path for SQLite")
    ap.add_argument("--status", action="store_true",
                    help="list what is applied and what is not, and change nothing")
    args = ap.parse_args()

    migrations = discover()
    con = db.connect(args.db)
    if args.status:
        done = applied(con)
        for m in migrations:
            print(f"{'applied' if m.id in done else 'PENDING':>7}  {m.id}")
        outstanding = [m.id for m in migrations if m.id not in done]
        print(f"\n{len(migrations) - len(outstanding)}/{len(migrations)} applied"
              f" on {con.dialect}" + (f", prefix {con.prefix!r}" if con.prefix else ""))
        return 0

    stamped = adopt(con, migrations)
    for mid in stamped:
        print(f"adopted {mid} — already present, recorded without running")
    ran = run(con, migrations)
    for mid in ran:
        print(f"applied {mid}")
    if not ran:
        print("nothing to do")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
