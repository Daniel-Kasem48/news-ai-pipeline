import json
import threading
import time
import urllib.error
import urllib.request
from tempfile import NamedTemporaryFile

from newsai import store
from newsai.server import RunState, make_server


def request(base, method, path, key=None):
    req = urllib.request.Request(base + path, method=method)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def serve(state):
    httpd = make_server("127.0.0.1", 0, state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


def test_health_is_public_but_status_needs_key():
    state = RunState(db="sqlite://", api_key="secret", run_args=[])
    httpd, base = serve(state)
    try:
        assert request(base, "GET", "/health")[0] == 200
        assert request(base, "GET", "/status")[0] == 401
        assert request(base, "GET", "/status", "secret")[0] == 200
    finally:
        httpd.shutdown()


def test_manual_run_is_keyed_and_does_not_overlap():
    started = threading.Event()
    release = threading.Event()

    def runner(args):
        started.set()
        release.wait(2)
        return 0

    state = RunState(db="sqlite://", api_key="secret", run_args=[], runner=runner)
    httpd, base = serve(state)
    try:
        assert request(base, "POST", "/runs")[0] == 401
        assert request(base, "POST", "/runs", "secret")[0] == 202
        assert started.wait(1)
        assert request(base, "POST", "/runs", "secret")[0] == 409
        release.set()
        time.sleep(0.1)
    finally:
        httpd.shutdown()


def test_status_reads_latest_run_from_db():
    with NamedTemporaryFile(suffix=".db") as f:
        con = store.connect(f.name)
        con.execute(
            "INSERT INTO runs (run_id, started_at, model_a, model_b, prompt_a_hash, prompt_b_hash, note)"
            " VALUES (?,?,?,?,?,?,?)",
            ("run-new", "2026-09-01T09:00:00+00:00", "a", "b", "pa", "pb", "ok"),
        )
        con.commit()
        con.close()

        state = RunState(db=f.name, api_key="secret", run_args=[])
        httpd, base = serve(state)
        try:
            code, body = request(base, "GET", "/status", "secret")
            assert code == 200
            assert body["latest_run"]["run_id"] == "run-new"
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    test_health_is_public_but_status_needs_key()
    test_manual_run_is_keyed_and_does_not_overlap()
    test_status_reads_latest_run_from_db()
    print("3/3 passed")
