"""The schema, in both dialects.

run_id is on everything: re-run the same posts with a changed prompt and compare
runs side by side. That is the whole point of the rig.

The schema itself is in `migrations/`, one numbered pair of files per change —
see migrate.py. Two dialects written out rather than one generated from the
other, because the differences are not mechanical and hiding them behind a
generator would hide the decisions:

  * SQLite has one text type and no length. MySQL cannot index a TEXT column, so
    every primary key here had to be given a VARCHAR width. The widths are set
    from the corpus (`posts.id` runs to 10 characters) with room to spare.
  * The MySQL tables are utf8mb4_bin, not the server default. See db.py — the
    default collation ignores diacritics, and half these keys are Arabic.
  * SQLite's rowid gave `runs` a free insertion order. MySQL has no equivalent,
    so report.py tie-breaks on run_id, which is `run-<ISO timestamp>` and sorts
    the same way.

Table names are written bare in the migrations and prefixed on the way to the
server — see db.TABLES. A MySQL database is usually shared, and `posts`, `runs`,
`labels` and `stories` are the names an application framework picks for its own.
"""
import json
from pathlib import Path

from newsai import db
from newsai import migrate as migrate_mod

DB = Path(__file__).resolve().parent.parent / "newsai.db"

def connect(target=None):
    """Open the database and bring it to the current schema.

    Migrating on connect rather than in a deploy step is deliberate: there is
    then no way to run this code against a database that has not been migrated,
    and no ordering to get right between the two.
    """
    con = db.connect(target if target is not None else str(DB))
    migrate_mod.run(con)
    return con


def j(value):
    """JSON-encode lists/dicts for storage; pass scalars through."""
    if value is None or isinstance(value, (str, int, float)):
        return value
    return json.dumps(value, ensure_ascii=False)


def migrate(con):
    """Apply anything outstanding. Kept here because callers already have it;
    the mechanism is migrate.py."""
    return migrate_mod.run(con)
