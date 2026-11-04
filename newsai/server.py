"""Tiny HTTP wrapper for scheduling and monitoring the pipeline."""
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from newsai import clock
from newsai import digest
from newsai import store


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def truthy(value):
    return str(value).lower() not in ("", "0", "false", "no", "off")


def default_run_args():
    cmd = [
        sys.executable, "-m", "newsai.run",
        "--stream", os.environ.get("NEWSAI_STREAM", "live"),
    ]
    if os.environ.get("NEWSAI_FIXTURES"):
        cmd += ["--fixtures", os.environ["NEWSAI_FIXTURES"]]
    return cmd


@dataclass
class RunState:
    db: str
    api_key: str
    run_args: list
    interval: int = 3600
    runner: object = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False
    last_start: str | None = None
    last_end: str | None = None
    last_exit: int | None = None
    last_error: str | None = None
    last_reason: str | None = None
    next_run_at: str | None = None

    def start(self, reason="manual"):
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.last_start = now()
            self.last_end = self.last_exit = self.last_error = None
            self.last_reason = reason
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        try:
            runner = self.runner or (lambda args: subprocess.run(args, check=False).returncode)
            code = runner(list(self.run_args))
            error = None
        except Exception as e:
            code, error = 1, str(e)
        with self.lock:
            self.running = False
            self.last_end = now()
            self.last_exit = code
            self.last_error = error

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "last_start": self.last_start,
                "last_end": self.last_end,
                "last_exit": self.last_exit,
                "last_error": self.last_error,
                "last_reason": self.last_reason,
                "next_run_at": self.next_run_at,
            }


def latest_run(db):
    con = store.connect(db)
    try:
        row = con.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        out = dict(row)
        run_id = out["run_id"]
        out["posts"] = con.execute(
            "SELECT COUNT(*) AS c FROM classifications WHERE run_id=?", (run_id,)
        ).fetchone()["c"]
        out["stories"] = con.execute(
            "SELECT COUNT(*) AS c FROM stories WHERE run_id=?", (run_id,)
        ).fetchone()["c"]
        return out
    finally:
        con.close()


def latest_stories(db, window=60, min_channels=1, limit=10):
    con = store.connect(db)
    try:
        row = con.execute("SELECT MAX(ts) AS t FROM posts WHERE ts IS NOT NULL").fetchone()
        end = clock.parse(row["t"] if row else None)
        if end is None:
            return []
        _, stories = digest.select(con, end, window, min_channels)
        return [{
            "id": s["id"],
            "title": s["title"],
            "channels": sorted(s["channels"]),
            "posts": s["posts"],
            "links": s["links"],
            "first": s["first"],
            "last": s["last"],
        } for s in stories[:limit]]
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s %s" % (self.address_string(), fmt % args), file=sys.stderr)

    def ok(self, body, status=200):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authed(self):
        key = self.server.state.api_key
        return key and self.headers.get("X-API-Key") == key

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self.ok({"ok": True})
        if not self.authed():
            return self.ok({"error": "unauthorized"}, 401)
        if path == "/status":
            body = self.server.state.snapshot()
            body["latest_run"] = latest_run(self.server.state.db)
            return self.ok(body)
        if path == "/runs/latest":
            return self.ok({"run": latest_run(self.server.state.db)})
        if path == "/stories/latest":
            q = parse_qs(urlparse(self.path).query)
            body = latest_stories(
                self.server.state.db,
                float(q.get("window", ["60"])[0]),
                int(q.get("min_channels", ["1"])[0]),
                int(q.get("limit", ["10"])[0]),
            )
            return self.ok({"stories": body})
        return self.ok({"error": "not found"}, 404)

    def do_POST(self):
        if not self.authed():
            return self.ok({"error": "unauthorized"}, 401)
        if urlparse(self.path).path != "/runs":
            return self.ok({"error": "not found"}, 404)
        if not self.server.state.start("manual"):
            return self.ok({"error": "run already active"}, 409)
        return self.ok({"started": True}, 202)


def make_server(host, port, state):
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.state = state
    return httpd


def schedule(state):
    while True:
        state.next_run_at = datetime.fromtimestamp(time.time() + state.interval, timezone.utc).isoformat(timespec="seconds")
        time.sleep(state.interval)
        state.start("schedule")


def main():
    api_key = os.environ.get("NEWSAI_API_KEY", "")
    if not api_key:
        raise SystemExit("NEWSAI_API_KEY not set")
    target = os.environ.get("DATABASE_URL", "newsai.db")

    # Migrate before serving, not on the first run that happens to touch the
    # database. A missing GRANT or a broken migration then fails here, loudly,
    # at deploy time — rather than an hour later inside a scheduled run, with a
    # container that has been reporting itself healthy the whole time.
    store.connect(target).close()

    state = RunState(
        db=target,
        api_key=api_key,
        run_args=default_run_args(),
        interval=int(os.environ.get("NEWSAI_INTERVAL_SECONDS", "3600")),
    )
    threading.Thread(target=schedule, args=(state,), daemon=True).start()
    if truthy(os.environ.get("NEWSAI_RUN_ON_START", "1")):
        state.start("startup")
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    try:
        make_server(host, port, state).serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
