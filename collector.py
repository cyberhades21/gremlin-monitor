"""Receives what `submit` sends from an editor and keeps every submission.

The editor reaches this only through the `ingest` door, as POST /submit. The session is decided
by the key sent with it (each session has its own), so one session cannot submit as another.
Nothing is ever overwritten or deleted here, and the editor has no way to read it back.
"""

import hmac
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYS = pathlib.Path("/keys")  # <session>/wakapi.key, mounted read-only
STORE = pathlib.Path("/submissions")
MAX_BYTES = 5 * 1024 * 1024


def session_for(authorization):
    """The session whose key this is, or None."""
    if not authorization.startswith("Bearer "):
        return None
    given = authorization[len("Bearer "):].strip().encode()
    for folder in sorted(KEYS.iterdir()):
        key_file = folder / "wakapi.key"
        if key_file.is_file() and hmac.compare_digest(key_file.read_bytes().strip(), given):
            return folder.name
    return None


def save(session, body):
    folder = STORE / session
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for attempt in range(1000):
        name = f"{stamp}.diff" if attempt == 0 else f"{stamp}-{attempt}.diff"
        try:
            with open(folder / name, "xb") as out:  # "x": never overwrite an earlier one
                out.write(body)
            return name
        except FileExistsError:
            continue
    raise RuntimeError("too many submissions in one second")


class Handler(BaseHTTPRequestHandler):
    timeout = 30

    def reply(self, status, text):
        data = (text + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/submit":
            return self.reply(404, "not found")
        session = session_for(self.headers.get("Authorization", ""))
        if session is None:
            return self.reply(401, "unknown session key")
        length = self.headers.get("Content-Length", "")
        if not length.isdigit():
            return self.reply(411, "length required")
        if int(length) > MAX_BYTES:
            return self.reply(413, "the changes are larger than 5 MB")
        name = save(session, self.rfile.read(int(length)))
        print(f"saved submission {session}/{name} ({length} bytes)", flush=True)
        self.reply(201, f"{session}/{name} ({length} bytes)")

    def log_message(self, *args):  # the line printed above is the only record needed
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
