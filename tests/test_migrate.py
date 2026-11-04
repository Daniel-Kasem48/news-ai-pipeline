"""The migration runner.

Two things are being checked, and they fail differently.

The mechanism — ordering, pairing, refusing a half-written migration — is
tested against synthetic files in a temp directory, so the cases stay readable
and do not change every time a real migration is added.

The real `migrations/` directory is then applied for real: to an empty database,
and to one built before the ledger existed. That second case is the one with
teeth. Getting it wrong does not raise — it replays an ALTER against a table
that already has the column, and on MySQL the failure lands after the DDL has
already been committed.

Runs on MySQL too when TEST_MYSQL_URL is set:

    TEST_MYSQL_URL=mysql://root:devroot@127.0.0.1:13306/newsai python -m tests.test_migrate
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from newsai import db
from newsai import migrate

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def write(directory, name, body):
    (Path(directory) / name).write_text(body)


# ---------------------------------------------------------------- mechanism

@case
def discovers_pairs_in_numeric_order():
    with tempfile.TemporaryDirectory() as d:
        for num, name in (("0010", "later"), ("0002", "earlier")):
            for dialect in ("sqlite", "mysql"):
                write(d, f"{num}_{name}.{dialect}.sql", "SELECT 1;")
        got = [m.id for m in migrate.discover(d)]
    return got == ["0002_earlier", "0010_later"], f"got {got}"


@case
def a_migration_missing_a_dialect_is_refused():
    """Half a migration is worse than none: it applies on the engine it was
    written for, records itself done, and never runs on the other."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "0001_half.sqlite.sql", "SELECT 1;")
        try:
            migrate.discover(d)
        except ValueError as e:
            return "mysql" in str(e), f"raised, but says {e!r}"
    return False, "accepted a migration with no mysql file"


@case
def a_stray_filename_is_refused():
    """Silently ignoring a file that does not match means a migration someone
    wrote never runs and nothing says so."""
    with tempfile.TemporaryDirectory() as d:
        write(d, "add_column.sql", "SELECT 1;")
        try:
            migrate.discover(d)
        except ValueError as e:
            return "add_column.sql" in str(e), f"raised, but says {e!r}"
    return False, "accepted a file with no number"


@case
def a_reused_number_is_refused():
    with tempfile.TemporaryDirectory() as d:
        for name in ("0001_one", "0001_two"):
            for dialect in ("sqlite", "mysql"):
                write(d, f"{name}.{dialect}.sql", "SELECT 1;")
        try:
            migrate.discover(d)
        except ValueError as e:
            return "duplicate" in str(e), f"raised, but says {e!r}"
    return False, "accepted two migrations numbered 0001"


# ------------------------------------------------------------------ applying

@case
def an_empty_database_gets_every_migration(target):
    con = db.connect(target)
    ran = migrate.run(con)
    ids = [m.id for m in migrate.discover()]
    con.close()
    return ran == ids, f"applied {ran}, want {ids}"


@case
def running_again_does_nothing(target):
    con = db.connect(target)
    migrate.run(con)
    again = migrate.run(con)
    con.close()
    return again == [], f"second run applied {again}"


@case
def a_pending_migration_is_applied_once(target):
    """The case the ledger exists for. CREATE INDEX is not idempotent on either
    engine, and MySQL commits DDL implicitly — a replay leaves the index there
    and the run failed."""
    con = db.connect(target)
    migrate.run(con)
    with tempfile.TemporaryDirectory() as d:
        for dialect in ("sqlite", "mysql"):
            write(d, f"9001_tmp_index.{dialect}.sql",
                  "-- probe: posts.ix_absent\n"
                  "CREATE INDEX ix_tmp_probe ON posts (channel);\n")
        extra = migrate.discover() + migrate.discover(d)
        first = migrate.run(con, extra)
        second = migrate.run(con, extra)
    ok = first == ["9001_tmp_index"] and second == []
    con.execute("DROP INDEX ix_tmp_probe ON posts" if con.dialect == db.MYSQL
                else "DROP INDEX ix_tmp_probe")
    con.execute("DELETE FROM schema_migrations WHERE id=?", ("9001_tmp_index",))
    con.commit()
    con.close()
    return ok, f"first={first} second={second}"


@case
def a_database_predating_the_ledger_is_adopted(target):
    """A database created before migrations existed has the tables and no
    ledger. Replaying 0001 against it would not raise — CREATE TABLE IF NOT
    EXISTS is a no-op — but replaying 0002's ALTER would."""
    con = db.connect(target)
    migrate.run(con)
    con.execute("DELETE FROM schema_migrations")     # forget we ever migrated
    con.commit()
    stamped = migrate.adopt(con)
    ran = migrate.run(con)
    ids = [m.id for m in migrate.discover()]
    con.close()
    return (stamped == ids and ran == [],
            f"adopted {stamped} then ran {ran}; want all adopted and nothing run")


@case
def adoption_stops_at_the_first_real_gap(target):
    """The half-old database: tables and 0002 present, 0003 never run.
    Everything up to the gap is stamped; the rest has to actually run.

    Note what is dropped — every column 0003 adds, not just the probed one. A
    migration is applied or it is not; the probe reports the whole entry, and a
    database sitting inside one is a state the ledger exists to prevent.
    """
    con = db.connect(target)
    migrate.run(con)
    con.execute("DELETE FROM schema_migrations")
    con.execute("ALTER TABLE classifications DROP COLUMN cited_source")
    for col in ("post_id", "channel", "cited_source"):
        con.execute(f"ALTER TABLE developments DROP COLUMN {col}")
    con.commit()

    stamped = migrate.adopt(con)
    ran = migrate.run(con)
    has = migrate._has_column(con, "developments", "cited_source")
    con.close()
    return (stamped == ["0001_initial", "0002_post_timestamps"]
            and ran == ["0003_source_provenance"] and has,
            f"adopted {stamped}, ran {ran}, column back: {has}")


@case
def adoption_is_a_no_op_once_anything_is_recorded(target):
    con = db.connect(target)
    migrate.run(con)
    stamped = migrate.adopt(con)
    con.close()
    return stamped == [], f"adopted {stamped} on a database that was already tracked"


def run_against(label, target, reset):
    fails = 0
    for fn in CASES:
        try:
            if fn.__code__.co_argcount:
                reset()
                ok, why = fn(target)
            else:
                ok, why = fn()
        except Exception as e:
            ok, why = False, f"{type(e).__name__}: {e}"
        print(f"{'ok  ' if ok else 'FAIL'} {label:7s} {fn.__name__:46s} {'' if ok else why}")
        fails += not ok
    return fails


def main():
    fails = 0

    path = tempfile.mktemp(suffix=".db")

    def fresh_sqlite():
        if os.path.exists(path):
            os.unlink(path)

    fails += run_against("sqlite", f"sqlite:///{path}", fresh_sqlite)
    fresh_sqlite()

    url = os.environ.get("TEST_MYSQL_URL")
    if url:
        def fresh_mysql():
            con = db.connect(url)
            rows = con.execute("SELECT table_name AS t FROM information_schema.tables"
                               " WHERE table_schema = DATABASE()").fetchall()
            con.execute("SET FOREIGN_KEY_CHECKS=0")
            for r in rows:
                con.execute(f"DROP TABLE IF EXISTS `{r['t']}`")
            con.execute("SET FOREIGN_KEY_CHECKS=1")
            con.commit()
            con.close()
        fails += run_against("mysql", url, fresh_mysql)
    else:
        print("\nskipped mysql — set TEST_MYSQL_URL to cover the dialect production uses")

    total = len(CASES) * (2 if url else 1)
    print(f"\n{total - fails}/{total} passed")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    main()
