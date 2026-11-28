"""The interviewer console. Run from the repository folder:

    python interviewer/app.py                 (needs Python 3.11+ and Docker; nothing to install)
    python interviewer/app.py --new-password  (make a new sign-in password)

It listens on this computer only, at http://localhost:8400.
"""

import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sys
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pages  # noqa: E402
import sessions  # noqa: E402

PORT = int(os.environ.get("GREMLIN_INTERVIEWER_PORT", "8400"))
ADMIN = sessions.DATA / "admin.json"
STATIC = pathlib.Path(__file__).resolve().parent / "static"
SIGNED_IN_SECONDS = 12 * 3600
MAX_BODY = 4096

_logins = {}  # token -> (csrf, expires)
_failures = []  # times of recent wrong passwords
_lock = threading.Lock()


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def set_password(password):
    salt = secrets.token_hex(16)
    ADMIN.parent.mkdir(parents=True, exist_ok=True)
    ADMIN.write_text(json.dumps({"salt": salt, "hash": hash_password(password, salt)}), encoding="utf-8")


def ensure_password():
    chosen = os.environ.get("GREMLIN_INTERVIEWER_PASSWORD")
    if chosen:
        set_password(chosen)
    elif "--new-password" in sys.argv or not ADMIN.is_file():
        password = secrets.token_urlsafe(12)
        set_password(password)
        print(f"Interviewer password (shown once): {password}", flush=True)


def password_ok(password):
    saved = json.loads(ADMIN.read_text(encoding="utf-8"))
    return hmac.compare_digest(saved["hash"], hash_password(password, saved["salt"]))


def shown_status(record):
    if record["status"] == "ready" and not sessions.editor_running(record["id"]):
        return "stopped"
    return record["status"]


def last_activity(record):
    try:
        events = sessions.read(record)["events"] if record["shown_status"] == "ended" else (
            sessions._heartbeats(record) if record["shown_status"] == "ready" else [])
    except (OSError, ValueError):
        return ""
    return pages.clock(events[-1]["time"]) if events else ""


class Handler(BaseHTTPRequestHandler):
    timeout = 60
    server_version = "gremlin-interviewer"
    sys_version = ""

    # ------------------------------------------------------------ replies

    def reply(self, status, body, content_type="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
                         "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def redirect(self, to, headers=()):
        self.reply(303, "", headers=[("Location", to), *headers])

    def not_found(self):
        self.reply(404, pages.message("Not found", "There is no such page.", self.csrf()))

    # ------------------------------------------------------------ who is asking

    def host_ok(self):
        # "localhost" only. Browsers keep cookies per host name, not per port, so this keeps the
        # console's cookie away from the editors, which candidates control, at 127.0.0.1:82xx.
        # It also stops other web sites from reaching the console by pointing a name at this computer.
        host = self.headers.get("Host", "")
        if host == f"localhost:{PORT}":
            return True
        if host == f"127.0.0.1:{PORT}" and self.command == "GET":
            self.redirect(f"http://localhost:{PORT}{self.path}")
        else:
            self.reply(421, "Open the console at http://localhost:%d\n" % PORT, "text/plain; charset=utf-8")
        return False

    def token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["gi"].value if "gi" in cookie else ""

    def csrf(self):
        with _lock:
            entry = _logins.get(self.token())
        return entry[0] if entry and entry[1] > time.time() else ""

    def form(self):
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or int(length) > MAX_BODY:
            return None
        body = self.rfile.read(int(length)).decode("utf-8", "replace")
        return {key: values[0] for key, values in urllib.parse.parse_qs(body, keep_blank_values=True).items()}

    # ------------------------------------------------------------ GET

    def do_GET(self):
        if not self.host_ok():
            return
        url = urllib.parse.urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        if len(parts) == 2 and parts[0] == "static" and parts[1] in ("style.css", "app.js"):
            kind = "text/css" if parts[1].endswith(".css") else "text/javascript"
            return self.reply(200, (STATIC / parts[1]).read_bytes(), f"{kind}; charset=utf-8")
        if url.path == "/favicon.ico":
            return self.reply(204, b"", "image/x-icon")
        if url.path == "/login":
            return self.reply(200, pages.login())
        csrf = self.csrf()
        if not csrf:
            return self.redirect("/login")
        if not parts:
            records = sessions.all_sessions()
            for record in records:
                record["shown_status"] = shown_status(record)
            activity = {r["id"]: last_activity(r) for r in records}
            return self.reply(200, pages.session_list(records, activity, csrf))
        if parts == ["new"]:
            return self.reply(200, pages.new_session(sessions.load_catalog(), csrf))
        if parts[0] != "sessions" or len(parts) < 2:
            return self.not_found()
        try:
            record = sessions.load(parts[1])
        except KeyError:
            return self.not_found()
        record["shown_status"] = shown_status(record)
        if len(parts) == 2:
            chosen = urllib.parse.parse_qs(url.query).get("submission", [""])[0]
            return self.reply(200, pages.session_detail(record, sessions.read(record), csrf, chosen))
        if parts[2:] == ["delete"]:
            return self.reply(200, pages.confirm_delete(record, csrf))
        if len(parts) == 4 and parts[2] == "submissions" and sessions.SUBMISSION_NAME.fullmatch(parts[3]):
            text = sessions.read(record)["submissions"].get(parts[3])
            if text is not None:
                return self.reply(200, text, "text/plain; charset=utf-8",
                                  [("Content-Disposition", f'attachment; filename="{record["id"]}-{parts[3]}"')])
        return self.not_found()

    # ------------------------------------------------------------ POST

    def do_POST(self):
        if not self.host_ok():
            return
        form = self.form()
        if form is None:
            return self.reply(400, "Bad request\n", "text/plain; charset=utf-8")
        if self.path == "/login":
            return self.sign_in(form.get("password", ""))
        csrf = self.csrf()
        if not csrf:
            return self.redirect("/login")
        if not hmac.compare_digest(form.get("csrf", ""), csrf):
            return self.reply(403, pages.message("Not allowed", "This form has expired. Reload the page.", csrf))
        parts = [p for p in self.path.split("/") if p]
        if parts == ["logout"]:
            with _lock:
                _logins.pop(self.token(), None)
            return self.redirect("/login", [("Set-Cookie", "gi=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")])
        if parts == ["sessions"]:
            try:
                sid = sessions.create(form.get("project", ""), form.get("label", ""))
            except (ValueError, RuntimeError) as error:
                return self.reply(400, pages.new_session(sessions.load_catalog(), csrf, str(error)))
            return self.redirect(f"/sessions/{sid}")
        if len(parts) == 3 and parts[0] == "sessions":
            try:
                record = sessions.load(parts[1])
            except KeyError:
                return self.not_found()
            action, status = parts[2], shown_status(record)
            if action == "end" and status == "ready":
                sessions.end(record["id"])
            elif action == "start" and status in ("stopped", "failed"):
                sessions.start_again(record["id"])
            elif action == "delete" and status not in ("starting", "ending", "deleting"):
                sessions.delete(record["id"])
                return self.redirect("/")
            return self.redirect(f"/sessions/{record['id']}")
        return self.not_found()

    def sign_in(self, password):
        with _lock:
            _failures[:] = [t for t in _failures if t > time.time() - 60]
            blocked = len(_failures) >= 10
        if blocked:
            return self.reply(429, pages.login("Too many wrong passwords. Wait a minute."))
        if not password_ok(password):
            with _lock:
                _failures.append(time.time())
            time.sleep(1)
            return self.reply(401, pages.login("That password is not right."))
        token = secrets.token_urlsafe(32)
        with _lock:
            _logins[token] = (secrets.token_urlsafe(32), time.time() + SIGNED_IN_SECONDS)
        return self.redirect("/", [("Set-Cookie", f"gi={token}; Path=/; HttpOnly; SameSite=Strict")])

    def log_message(self, fmt, *args):  # one short line per request, never the form contents
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    sessions.load_catalog()  # fail now, not at the first click, if the catalogue is wrong
    ensure_password()
    sessions.recover()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Interviewer console: http://localhost:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
