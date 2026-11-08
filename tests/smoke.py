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

    def register(self, username, password="test-new-account-password"):
        status, profile = self.call("/register", "POST", {"username": username, "password": password})
        assert status == 201 and profile["user"] == username and profile["boards"] == [], (status, profile)
        self.csrf = profile["csrf"]
        return profile

    def login_registered(self, username, password="test-new-account-password"):
        status, profile = self.call("/login", "POST", {"username": username, "password": password})
        assert status == 200 and profile["user"] == username, (status, profile)
        self.csrf = profile["csrf"]
        return profile

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


def browser_members(browser, base, revision):
    from playwright.sync_api import expect

    frames = [[], []]
    pages = []
    for index, user in enumerate(("alice", "bob")):
        page = browser.new_context().new_page()
        page.on("websocket", lambda socket, inbox=frames[index]:
                socket.on("framereceived", lambda payload: inbox.append(json.loads(payload))))
        page.goto(base)
        expect(page.locator("#team-guide")).to_be_hidden()
        page.locator("#login input[name=username]").fill(user)
        page.locator("#login input[name=password]").fill(PASSWORDS[user])
        page.locator("#login button").click()
        expect(page.locator("#board")).to_have_attribute("data-revision", str(revision))
        expect(page.get_by_role("heading", name="एक कार्ड का स्थान बदलें / Move a card")).to_be_visible()
        expect(page.locator("#studio-guide")).to_be_hidden()
        pages.append(page)
    return pages, frames


def seen_revision(pages, frames, revision):
    from playwright.sync_api import expect

    for index, page in enumerate(pages):
        expect(page.locator("#board")).to_have_attribute("data-revision", str(revision), timeout=6000)
        assert {"v": 1, "type": "changed", "board": "team", "revision": revision} in frames[index], frames[index]


def browser_move(base):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        pages, frames = browser_members(browser, base, 1)
        for page in pages:
            expect(page.locator('[data-task="101"]')).to_contain_text("पहला कार्ड 🧪")
            expect(page.locator('[data-task="101"] img')).to_have_count(0)
        alice, _ = pages
        alice.locator('[data-task="101"] [data-action=move]').click()
        alice.locator("select[name=destination]").select_option("2/12")
        alice.locator('#move button[type="submit"]').click()
        seen_revision(pages, frames, 2)
        for page in pages:
            expect(page.locator('[data-lane="2"] [data-column="12"] [data-task="101"]')).to_be_visible()
        browser.close()


def browser_cards(base, revision):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        pages, frames = browser_members(browser, base, revision)
        alice, bob = pages
        destination = '[data-lane="2"] [data-column="12"]'
        title = "नया काम 🧪"
        alice.locator(destination + " [data-action=create]").click()
        alice.locator("#card-form input[name=title]").fill(title)
        alice.locator("#card-form textarea").fill("कार्ड का विवरण")
        alice.locator('#card-form button[type="submit"]').click()
        seen_revision(pages, frames, revision + 1)
        identity = int(bob.locator(destination + " .card").filter(has_text=title).get_attribute("data-task"))
        card = f'[data-task="{identity}"]'
        archived = f'[data-archived-task="{identity}"]'
        original_order = bob.locator(destination + " [data-task]").evaluate_all("(cards) => cards.map(c => c.dataset.task)")

        # Hold Alice's draft across Bob's commit and its resulting live re-render.
        for page in pages:
            page.locator(card + " [data-action=edit]").click()
        alice.locator("#card-form input[name=title]").fill("Alice's unsaved draft")
        bob.locator("#card-form input[name=title]").fill("नया शीर्षक")
        bob.locator("#card-form textarea").fill("<img src=x onerror=alert(1)>")
        bob.locator('#card-form button[type="submit"]').click()
        seen_revision(pages, frames, revision + 2)
        alice.locator('#card-form button[type="submit"]').click()
        expect(alice.locator("#card-conflict")).to_be_visible()
        expect(alice.locator("#card-form input[name=title]")).to_have_value("Alice's unsaved draft")
        alice.locator("#cancel-card").click()
        for page in pages:
            expect(page.locator(card)).to_contain_text("नया शीर्षक")
            expect(page.locator(card)).to_contain_text("<img src=x onerror=alert(1)>")
            expect(page.locator(card + " img")).to_have_count(0)

        alice.locator(card + " [data-action=archive]").click()
        seen_revision(pages, frames, revision + 3)
        for page in pages:
            expect(page.locator(card)).to_have_count(0)
            page.locator("#archive-summary").click()
            expect(page.locator(archived)).to_contain_text("नया शीर्षक")
        bob.locator(archived + " [data-action=restore]").click()
        seen_revision(pages, frames, revision + 4)
        for page in pages:
            expect(page.locator(archived)).to_have_count(0)
            expect(page.locator(destination + " " + card)).to_contain_text("नया शीर्षक")
            assert page.locator(destination + " [data-task]").evaluate_all(
                "(cards) => cards.map(c => c.dataset.task)") == original_order

        alice.locator(card + " [data-action=archive]").click()
        seen_revision(pages, frames, revision + 5)
        browser.close()
        return identity


def api_cards(alice, bob, revision):
    payload = {"title": "नया काम", "description": "", "lane": 2, "column": 12, "revision": revision}
    assert alice.call(TEAM + "/tasks", "POST", dict(payload, title=" "))[0] == 422
    assert alice.call(TEAM + "/tasks", "POST", dict(payload, description="a" * 4097))[0] == 422
    status, created = alice.call(TEAM + "/tasks", "POST", payload)
    assert status == 201 and created["revision"] == revision + 1
    task = TEAM + "/tasks/" + str(created["id"])
    edit = {"title": "नया शीर्षक", "description": "नया विवरण", "revision": revision + 1}
    assert bob.call(task + "/details", "PATCH", edit)[0] == 200
    assert alice.call(task + "/details", "PATCH", edit)[0] == 409
    assert alice.call(task + "/archive", "POST", {"revision": revision + 2})[0] == 200
    assert alice.call(task + "/archive", "POST", {"revision": revision + 3})[0] == 404
    assert bob.call(task + "/restore", "POST", {"revision": revision + 3})[0] == 200
    assert bob.call(task + "/restore", "POST", {"revision": revision + 4})[0] == 404
    assert alice.call(task + "/archive", "POST", {"revision": revision + 4})[0] == 200
    return created["id"]


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
    # Simulate a previously deployed database with exactly its v1 schema and history.
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("ALTER TABLE boards DROP COLUMN archived")
        db.execute("DROP TABLE board_access")
        db.execute("DROP TABLE accounts")
        db.execute("DELETE FROM bhash_schema_history WHERE version>1")
    with server(command, database):
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT revision, lanes, archived FROM boards WHERE id='team'").fetchone() == (1, lanes, "[]")
            assert db.execute("SELECT version FROM bhash_schema_history ORDER BY version").fetchall() == [(1,), (2,), (3,)]
    print("PASS import order, rejected imports and v1 database upgrade", flush=True)


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

        revision = saved["revision"]
        task_id = browser_cards(base, revision) if browser else api_cards(alice, bob, revision)
        task = TEAM + "/tasks/" + str(task_id)
        saved = alice.call(TEAM)[1]
        assert saved["revision"] == revision + 5
        assert saved["archived"][0]["card"]["id"] == task_id
        valid = {"revision": saved["revision"], "lane": 2, "column": 12, "description": "", "title": " "}
        assert alice.call(TEAM + "/tasks", "POST", valid)[0] == 422
        assert alice.call(TEAM + "/tasks", "POST", dict(valid, title="Valid", description="a" * 4097))[0] == 422
        assert alice.call(task + "/archive", "POST", {"revision": saved["revision"]})[0] == 404
        denied = {"revision": saved["revision"]}
        for suffix, method in (("/details", "PATCH"), ("/archive", "POST"), ("/restore", "POST")):
            assert charlie.call(task + suffix, method, denied)[0] == 403
        assert charlie.call(TEAM + "/tasks", "POST", denied)[0] == 403
        assert charlie.call("/api/boards/studio")[1]["archived"] == []
        assert alice.call(task + "/restore", "POST", denied, csrf=False)[0] == 403
        assert alice.call(TEAM) == (200, saved)

    with server(command, database) as base:
        alice = Account(base, "alice")
        assert alice.call(TEAM) == (200, saved)
        assert Account(base, "bob").call(TEAM) == (200, saved)
        assert alice.call(task + "/restore", "POST", {"revision": saved["revision"]})[0] == 200
        restored = alice.call(TEAM)[1]
        assert restored["archived"] == []
        assert restored["lanes"][1]["columns"][1]["tasks"][-1]["id"] == task_id
    with server(command, database) as base:
        assert Account(base, "alice").call(TEAM) == (200, restored)
    print("PASS card lifecycle, two live sessions, CSRF, isolation, conflicts and restart", flush=True)


def invitation(owner, board, username, role):
    status, body = owner.call(f"/api/boards/{board}/invitations", "POST", {"username": username, "role": role})
    assert status == 201, (status, body)
    actual_board, token = body["code"].split(".")
    assert actual_board == board and len(token) == 64
    return {"board": board, "token": token}


def check_membership(command, tmp, browser):
    database = tmp / "members.db"
    accept = "/api/invitations/accept"
    with server(command, database, FIXTURE) as base:
        alice, bob, charlie = (Account(base, user) for user in ("alice", "bob", "charlie"))
        assert alice.call("/session")[1]["boards"] == [{"id": "team", "role": "owner"}]
        assert bob.call("/session")[1]["boards"] == [{"id": "team", "role": "editor"}]
        viewer, editor = Account(base), Account(base)
        assert viewer.call("/register", "POST", {"username": "new-viewer", "password": "test-new-account-password"}, csrf=False)[0] == 403
        assert viewer.call("/register", "POST", {"username": "invalid user", "password": "test-new-account-password"})[0] == 422
        viewer.register("new-viewer")
        assert Account(base).call("/register", "POST", {"username": "new-viewer", "password": "other-long-password"})[0] == 409
        editor.register("new-editor")
        assert viewer.call(TEAM)[0] == 403 and viewer.call("/api/boards/studio")[0] == 403
        with closing(viewer.live()) as denied:
            opcode, payload = denied.recv_data()
            assert opcode == websocket.ABNF.OPCODE_CLOSE and struct.unpack("!H", payload[:2])[0] == 1008
        proposal = {"username": "new-viewer", "role": "viewer"}
        assert bob.call(TEAM + "/invitations", "POST", proposal)[0] == 403
        assert alice.call("/api/boards/studio/invitations", "POST", proposal)[0] == 403
        assert alice.call(TEAM + "/invitations", "POST", dict(proposal, role="owner"))[0] == 422

        view_code = invitation(alice, "team", "new-viewer", "viewer")
        assert viewer.call(TEAM)[0] == 403
        assert editor.call(accept, "POST", view_code)[0] == 403
        assert viewer.call(accept, "POST", view_code, csrf=False)[0] == 403
        with closing(sqlite3.connect(database)) as db:
            stored, expiry = db.execute("SELECT invite_hash,expires FROM board_access WHERE board='team' AND username='new-viewer'").fetchone()
            assert stored.startswith("$bhash$") and view_code["token"] not in stored and expiry > 0
        assert viewer.call(accept, "POST", view_code)[0] == 204
        assert viewer.call(accept, "POST", view_code)[0] == 403
        assert viewer.call(TEAM)[0] == 200 and viewer.call("/api/boards/studio")[0] == 403
        assert viewer.call(TEAM + "/invitations", "POST", proposal)[0] == 403
        assert viewer.call(TEAM + "/tasks", "POST", {"revision": 1})[0] == 403
        assert viewer.call(TEAM + "/tasks/101/details", "PATCH", {"revision": 1})[0] == 403
        assert viewer.call(TEAM + "/tasks/101/archive", "POST", {"revision": 1})[0] == 403

        expired = invitation(alice, "team", "new-editor", "editor")
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE board_access SET expires=0 WHERE board='team' AND username='new-editor'")
        assert editor.call(accept, "POST", expired)[0] == 403
        edit_code = invitation(alice, "team", "new-editor", "editor")
        assert editor.call(accept, "POST", expired)[0] == 403
        assert editor.call(accept, "POST", edit_code)[0] == 204
        with closing(viewer.live()) as watch:
            assert json.loads(watch.recv())["type"] == "ready"
            assert editor.call(TEAM + "/tasks/101/details", "PATCH",
                               {"revision": 1, "title": "सदस्य का बदलाव", "description": ""})[0] == 200
            assert json.loads(watch.recv()) == {"v": 1, "type": "changed", "board": "team", "revision": 2}
        assert editor.call("/api/boards/studio")[0] == 403
        studio_code = invitation(charlie, "studio", "new-viewer", "editor")
        assert viewer.call(accept, "POST", studio_code)[0] == 204
        assert viewer.call("/api/boards/studio")[0] == 200
        assert charlie.call(TEAM)[0] == 403

    with server(command, database) as base:
        viewer = Account(base)
        profile = viewer.login_registered("new-viewer")
        assert profile["boards"] == [{"id": "studio", "role": "editor"}, {"id": "team", "role": "viewer"}]
        assert viewer.call(TEAM)[1]["revision"] == 2
        assert viewer.call(TEAM + "/tasks/101/archive", "POST", {"revision": 2})[0] == 403
        assert viewer.call("/api/boards/studio")[0] == 200
        assert viewer.call(accept, "POST", view_code)[0] == 403
        if browser:
            browser_invitation(base)
    print("PASS persistent registration, role enforcement, single-use invitations and HTTP/WebSocket isolation", flush=True)


def browser_invitation(base):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        owner = browser.new_context().new_page()
        guest = browser.new_context().new_page()
        owner.goto(base)
        owner.locator("#login input[name=username]").fill("alice")
        owner.locator("#login input[name=password]").fill(PASSWORDS["alice"])
        owner.locator("#login button").click()
        expect(owner.locator("#invite-owner")).to_be_visible()
        guest.goto(base)
        expect(guest.locator("#register")).to_be_visible()
        guest.locator("#register input[name=username]").fill("browser-guest")
        guest.locator("#register input[name=password]").fill("browser-guest-long-password")
        guest.locator("#register button").click()
        expect(guest.locator("#no-membership")).to_be_visible()
        expect(guest.locator("#board")).to_be_hidden()
        expect(guest.locator("#invite-owner")).to_be_hidden()

        owner.locator("#invite-owner summary").click()
        owner.locator("#issue-invite input[name=username]").fill("browser-guest")
        owner.locator("#issue-invite select[name=role]").select_option("viewer")
        owner.locator("#issue-invite button").click()
        expect(owner.locator("#invite-code")).to_be_visible()
        code = owner.locator("#invite-code").input_value()
        guest.locator("#membership details").first.locator("summary").click()
        guest.locator("#accept-invite input[name=code]").fill(code)
        guest.locator("#accept-invite button").click()
        expect(guest.locator("#board")).to_have_attribute("data-revision", "2")
        expect(guest.locator("#board")).to_contain_text("सदस्य का बदलाव")
        expect(guest.locator("#board [data-action]")).to_have_count(0)
        expect(guest.locator("#invite-owner")).to_be_hidden()
        browser.close()


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
            check_membership(command, Path(directory), args.browser)


if __name__ == "__main__":
    main()
