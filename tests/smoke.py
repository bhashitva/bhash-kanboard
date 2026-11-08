"""Exercise an already built Bhash board against an isolated SQLite database."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import http.cookiejar
import json
import os
from pathlib import Path
import queue
import sqlite3
import struct
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request

import websocket

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/get-board.json"
TEAM = "/api/boards/team"
PASSWORDS = {user: f"test-{user}-long-password" for user in ("alice", "bob", "charlie")}


@contextmanager
def server(command, database, imported=None):
    options = ["--local=true", "--port=0", f"--db={database}"]
    if imported:
        options.append(f"--import={imported}")
    env = dict(os.environ, **{f"BHASH_BOARD_{user.upper()}_PASSWORD": pw for user, pw in PASSWORDS.items()})
    with subprocess.Popen(command + options, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace") as process:
        ready = queue.Queue()
        logs = []

        def read_output():
            for line in process.stdout:
                logs.append(line)
                if line.startswith("READY "):
                    ready.put(int(line.split()[1]))
            ready.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            port = ready.get(timeout=60)
            assert port, "server exited before reporting readiness"
            yield f"http://127.0.0.1:{port}"
        except Exception:
            print("Server output:\n" + "".join(logs), flush=True)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            reader.join(timeout=5)


class Account:
    def __init__(self, base, user=None):
        self.base = base
        self.cookies = http.cookiejar.CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
        self.csrf = self.call("/session")[1]["csrf"]
        if user:
            status, profile = self.call("/login", "POST", {"username": user, "password": PASSWORDS[user]})
            assert status == 200 and profile["user"] == user, (status, profile)
            self.csrf = profile["csrf"]

    def call(self, path, method="GET", body=None, csrf=True):
        headers = {"Origin": self.base, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method != "GET" and csrf:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=None if body is None else json.dumps(body).encode("utf-8"))
        try:
            with self.client.open(req, timeout=15) as reply:
                return reply.status, json.load(reply) if reply.status != 204 else None
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", errors="replace")

    def live(self, board="team"):
        cookies = "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)
        return websocket.create_connection(self.base.replace("http:", "ws:") + "/live/" + board,
                                           origin=self.base, cookie=cookies, subprotocols=["bhash.board.v1"],
                                           timeout=10)


def layout(snapshot):
    return [(lane["id"], [(column["id"], [task["id"] for task in column["tasks"]])
                         for column in lane["columns"]]) for lane in snapshot["lanes"]]


def browser_move(base):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        frames = [[], []]
        pages = []
        for index, user in enumerate(("alice", "bob")):
            page = browser.new_context().new_page()
            page.on("websocket", lambda socket, inbox=frames[index]:
                    socket.on("framereceived", lambda payload: inbox.append(json.loads(payload))))
            page.goto(base)
            expect(page.locator("#team-guide")).to_be_hidden()
            page.locator("input[name=username]").fill(user)
            page.locator("input[name=password]").fill(PASSWORDS[user])
            page.locator("#login button").click()
            expect(page.locator("#board")).to_have_attribute("data-revision", "1")
            expect(page.get_by_role("heading", name="एक कार्ड का स्थान बदलें / Move a card")).to_be_visible()
            expect(page.locator("#studio-guide")).to_be_hidden()
            expect(page.locator('[data-task="101"]')).to_contain_text("पहला कार्ड 🧪")
            expect(page.locator('[data-task="101"] img')).to_have_count(0)
            pages.append(page)
        alice, bob = pages
        alice.locator('[data-task="101"] button').click()
        alice.locator("select[name=destination]").select_option("2/12")
        alice.locator('#move button[type="submit"]').click()
        for index, page in enumerate((alice, bob)):
            expect(page.locator('[data-lane="2"] [data-column="12"] [data-task="101"]')).to_be_visible(timeout=5000)
            expect(page.locator("#board")).to_have_attribute("data-revision", "2")
            assert {"v": 1, "type": "changed", "board": "team", "revision": 2} in frames[index], frames[index]
        browser.close()


def rejected_import(command, database, source, message):
    env = dict(os.environ, **{f"BHASH_BOARD_{user.upper()}_PASSWORD": pw for user, pw in PASSWORDS.items()})
    result = subprocess.run(command + ["--local=true", "--port=0", f"--db={database}", f"--import={source}"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=60, text=True, encoding="utf-8", errors="replace")
    assert result.returncode != 0 and "READY " not in result.stdout and message in result.stdout, result.stdout


def check_import(command, tmp):
    database = tmp / "import.db"
    broken = tmp / "bad.json"
    export = json.loads(FIXTURE.read_text(encoding="utf-8"))
    export["result"][1]["columns"][1]["tasks"][1]["id"] = 102
    broken.write_text(json.dumps(export), encoding="utf-8")
    rejected_import(command, database, broken, "कार्ड")
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT revision, lanes FROM boards WHERE id='team'").fetchone() == (0, "[]")
    with server(command, database, FIXTURE):
        with closing(sqlite3.connect(database)) as db:
            revision, lanes = db.execute("SELECT revision, lanes FROM boards WHERE id='team'").fetchone()
            assert revision == 1
            assert layout({"lanes": json.loads(lanes)}) == [(0, [(11, [101, 102]), (12, [])]),
                                                            (2, [(11, [103]), (12, [])])]
    rejected_import(command, database, FIXTURE, "आयातित")
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT revision, lanes FROM boards WHERE id='team'").fetchone() == (1, lanes)
    print("PASS import order, invalid import rollback and duplicate import rejection", flush=True)


def exercise(command, tmp, browser):
    database = tmp / "test.db"
    with server(command, database, FIXTURE) as base:
        alice, bob, charlie = [Account(base, user) for user in ("alice", "bob", "charlie")]
        status, snapshot = alice.call(TEAM)
        assert status == 200, (status, snapshot)
        assert snapshot["revision"] == 1 and layout(snapshot) == [(0, [(11, [101, 102]), (12, [])]),
                                                                  (2, [(11, [103]), (12, [])])], snapshot
        assert Account(base).call(TEAM)[0] in (401, 403)
        assert charlie.call(TEAM)[0] == 403
        assert charlie.call("/api/boards/studio")[1]["lanes"] == []
        assert alice.call("/api/boards/studio")[0] == 403
        change = {"lane": 2, "column": 12, "position": 1, "revision": 1}
        assert charlie.call(TEAM + "/tasks/101", "PATCH", change)[0] == 403
        assert alice.call(TEAM + "/tasks/101", "PATCH", change, csrf=False)[0] == 403
        with closing(charlie.live()) as forbidden:
            opcode, frame = forbidden.recv_data()
            assert opcode == websocket.ABNF.OPCODE_CLOSE and struct.unpack("!H", frame[:2])[0] == 1008, (opcode, frame)

        if browser:
            browser_move(base)
        else:
            with closing(alice.live()) as alice_live, closing(bob.live()) as bob_live:
                for channel in (alice_live, bob_live):
                    assert json.loads(channel.recv()) == {"v": 1, "type": "ready"}
                assert alice.call(TEAM + "/tasks/101", "PATCH", change) == (200, {"revision": 2})
                for channel in (alice_live, bob_live):
                    assert json.loads(channel.recv()) == {"v": 1, "type": "changed", "board": "team", "revision": 2}
        assert layout(bob.call(TEAM)[1]) == [(0, [(11, [102]), (12, [])]), (2, [(11, [103]), (12, [101])])]
        assert bob.call(TEAM + "/tasks/102", "PATCH", change)[0] == 409
        change["revision"] = 2
        assert bob.call(TEAM + "/tasks/102", "PATCH", dict(change, position=3))[0] == 422
        assert bob.call(TEAM + "/tasks/999", "PATCH", change)[0] == 404
        assert bob.call(TEAM + "/tasks/102", "PATCH", dict(change, column=99))[0] == 422
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(alice.call, TEAM + "/tasks/102", "PATCH", change)
            b = pool.submit(bob.call, TEAM + "/tasks/103", "PATCH", dict(change, position=2))
            results = [a.result(), b.result()]
        assert sorted(status for status, _ in results) == [200, 409], results
        current = alice.call(TEAM)[1]
        assert current["revision"] == 3
        assert layout(current) == ([(0, [(11, []), (12, [])]), (2, [(11, [103]), (12, [102, 101])])]
                                   if results[0][0] == 200 else
                                   [(0, [(11, [102]), (12, [])]), (2, [(11, []), (12, [101, 103])])]), current
        ids = layout(current)
        task = ids[1][1][1][1][0]
        assert bob.call(TEAM + f"/tasks/{task}", "PATCH", dict(change, revision=3, position=2))[0] == 200
        saved = alice.call(TEAM)[1]
        assert layout(saved)[1][1][1][1] == list(reversed(ids[1][1][1][1]))

    with server(command, database) as base:
        assert Account(base, "alice").call(TEAM) == (200, saved)
        assert Account(base, "bob").call(TEAM) == (200, saved)
    print("PASS import, two live sessions, CSRF, isolation, conflicts, same-column reorder and restart", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--import-only", action="store_true", help="Skip network tests")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("specify the built server command after --")
    scratch = ROOT / "out"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        check_import(command, Path(directory))
        if not args.import_only:
            exercise(command, Path(directory), args.browser)


if __name__ == "__main__":
    main()
