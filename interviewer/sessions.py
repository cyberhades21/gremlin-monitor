"""Interview sessions: starting, ending and deleting them, and reading what each one recorded.

Each session is its own copy of ../compose.yaml under its own project name (gremlin-<session>),
so sessions share nothing but the editor image. This module drives `docker compose`; it never
passes anything the browser sent into a command, only values from the catalogue or made here.
"""

import base64
import datetime
import json
import os
import pathlib
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent  # holds compose.yaml
DATA = HERE / "data"
SESSIONS = DATA / "sessions"
CATALOG = HERE / "catalog.json"

DIFFICULTIES = [
    ("easy", "Easy"),
    ("medium", "Medium"),
    ("hard", "Hard"),
    ("extra-hard", "Extra hard"),
    ("uncategorized", "Uncategorised"),
]
SERVICES = {"postgres", "mongodb"}
SESSION_ID = re.compile(r"^iv-[0-9]{6}-[0-9a-f]{6}$")
SUBMISSION_NAME = re.compile(r"^[0-9]{8}T[0-9]{6}Z(-[0-9]+)?\.diff$")

# Local requests must not go through any proxy set on this computer.
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_lock = threading.RLock()
_busy = set()  # sessions with a start, end or delete running right now
_cache = {}  # session id -> (time, record data)


def load_catalog():
    projects = json.loads(CATALOG.read_text(encoding="utf-8"))["projects"]
    known = {key for key, _ in DIFFICULTIES}
    for project in projects:
        for field in ("id", "name", "repo"):
            if not project.get(field):
                raise ValueError(f"catalog.json: a project is missing {field!r}")
        if not project["repo"].startswith("https://"):
            raise ValueError(f"catalog.json: {project['id']}: only https:// repositories work")
        if project.get("difficulty") not in known:
            project["difficulty"] = "uncategorized"
        if set(project.get("services", [])) - SERVICES:
            raise ValueError(f"catalog.json: {project['id']}: unknown service")
        for key, value in project.get("env", {}).items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or "\n" in value:
                raise ValueError(f"catalog.json: {project['id']}: bad env entry {key!r}")
        setup = project.get("setup", "")
        if "\n" in setup or "'" in setup:
            raise ValueError(f"catalog.json: {project['id']}: setup must be one line without single quotes")
    return projects


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- the session records


def folder(sid):
    if not SESSION_ID.fullmatch(sid):
        raise KeyError(sid)
    return SESSIONS / sid


def load(sid):
    path = folder(sid) / "session.json"
    if not path.is_file():
        raise KeyError(sid)
    with _lock:
        return json.loads(path.read_text(encoding="utf-8"))


def save(record):
    path = folder(record["id"]) / "session.json"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
        tmp.replace(path)


def update(sid, **changes):
    with _lock:
        record = load(sid)
        record.update(changes)
        save(record)
        return record


def all_sessions():
    if not SESSIONS.is_dir():
        return []
    records = []
    for child in SESSIONS.iterdir():
        try:
            records.append(load(child.name))
        except (KeyError, ValueError):
            continue
    return sorted(records, key=lambda r: r["created"], reverse=True)


# ---------------------------------------------------------------- docker


def compose(record, *args, timeout=900):
    env = dict(os.environ)
    env.update(
        SESSION_ID=record["id"],
        REPO_URL=record["repo"],
        REPO_REF=record["ref"],
        PASSWORD=record["password"],
        PORT=str(record["port"]),
        WAKAPI_PORT=str(record["wakapi_port"]),
        SESSION_ENV_FILE=(folder(record["id"]) / "session.env").as_posix(),
        COMPOSE_PROFILES=",".join(record["services"]),
    )
    command = ["docker", "compose", "--project-directory", str(ROOT), "-f", str(ROOT / "compose.yaml"),
               "-p", "gremlin-" + record["id"], *args]
    return subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def editor_running(sid):
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project=gremlin-{sid}",
         "--filter", "label=com.docker.compose.service=editor"],
        capture_output=True, text=True, timeout=30)
    return bool(result.stdout.strip())


def editor_answers(record):
    """True when the editor's own sign-in page loads through the front door."""
    request = urllib.request.Request(f"http://127.0.0.1:{record['port']}/login",
                                     headers={"Cookie": "gremlin_consent=1"})
    try:
        with _http.open(request, timeout=4) as response:
            return response.status == 200
    except OSError:
        return False


def free_port(start, taken):
    for port in range(start, start + 400):
        if port in taken:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("no free port found")


def tail(text, lines=12):
    return "\n".join([line for line in text.strip().splitlines() if line.strip()][-lines:])


# ---------------------------------------------------------------- start, end, delete


def in_background(sid, work):
    """Run one long job per session at a time. Returns False if one is already running."""
    with _lock:
        if sid in _busy:
            return False
        _busy.add(sid)

    def run():
        try:
            work(sid)
        except Exception as error:  # shown to the interviewer instead of being lost in a thread
            update(sid, status="failed", error=f"{type(error).__name__}: {error}")
        finally:
            with _lock:
                _busy.discard(sid)
            _cache.pop(sid, None)

    threading.Thread(target=run, daemon=True).start()
    return True


def create(project_id, label):
    project = next((p for p in load_catalog() if p["id"] == project_id), None)
    if project is None:
        raise ValueError("unknown project")
    label = " ".join(label.split())[:80]
    if not label:
        raise ValueError("a candidate label is needed")
    with _lock:
        taken = {value for record in all_sessions() for value in (record["port"], record["wakapi_port"])}
        sid = f"iv-{datetime.datetime.now():%y%m%d}-{secrets.token_hex(3)}"
        port = free_port(8200, taken)
        wakapi_port = free_port(3200, taken | {port})
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no look-alikes
        record = {
            "id": sid, "label": label, "project_id": project["id"], "project_name": project["name"],
            "repo": project["repo"], "ref": project.get("ref", ""), "services": project.get("services", []),
            "port": port, "wakapi_port": wakapi_port,
            "password": "".join(secrets.choice(alphabet) for _ in range(14)),
            "status": "starting", "created": now(), "error": "", "key": "",
            "setup": "pending" if project.get("setup") else "",
        }
        save(record)
    env_lines = [f"{key}={value}" for key, value in project.get("env", {}).items()]
    if project.get("setup"):
        env_lines.append(f"SETUP_COMMAND='{project['setup']}'")  # single quotes: taken literally
    (folder(sid) / "session.env").write_text("\n".join(env_lines) + "\n", encoding="utf-8", newline="\n")
    in_background(sid, _start)
    return sid


def _start(sid):
    record = update(sid, status="starting", error="")
    result = compose(record, "up", "-d")
    if result.returncode != 0:
        update(sid, status="failed", error=tail(result.stderr))
        return
    deadline = time.time() + 420  # a first clone can be slow
    while time.time() < deadline:
        if editor_answers(record):
            break
        if not editor_running(sid):
            logs = compose(record, "logs", "--no-log-prefix", "--tail", "15", "editor", timeout=60)
            update(sid, status="failed", error=tail(logs.stdout + logs.stderr))
            return
        time.sleep(3)
    else:
        update(sid, status="failed", error="The editor did not answer within 7 minutes.")
        return
    key = compose(record, "exec", "-T", "editor", "cat", "/shared/wakapi.key", timeout=60).stdout.strip()
    update(sid, key=key)
    if record.get("setup"):
        update(sid, setup=_wait_for_setup(record))
    update(sid, status="ready", ready_at=now())


def _wait_for_setup(record):
    """The project's preparation runs inside the editor; wait for it to finish."""
    deadline = time.time() + 900
    while time.time() < deadline:
        state = compose(record, "exec", "-T", "editor", "sh", "-c", "cat $HOME/.gremlin/setup", timeout=60).stdout.strip()
        if state in ("done", "failed"):
            return state
        if not editor_running(record["id"]):
            break
        time.sleep(5)
    return "failed"


def start_again(sid):
    update(sid, status="starting", error="")
    return in_background(sid, _start)


def end(sid):
    update(sid, status="ending", error="")
    return in_background(sid, _end)


def _end(sid):
    record = load(sid)
    notes = []
    if editor_running(sid):
        submitted = compose(record, "exec", "-T", "editor", "submit", timeout=240)
        if submitted.returncode != 0:
            notes.append("The final submit failed: " + tail(submitted.stdout + submitted.stderr, 3))
    else:
        notes.append("The editor was not running, so no final copy of the work was taken.")
    notes += _collect(record)
    stopped = compose(record, "stop", timeout=180)
    if stopped.returncode != 0:
        notes.append("Stopping failed: " + tail(stopped.stderr, 3))
    update(sid, status="ended", ended_at=now(), error="\n".join(notes))


def _collect(record):
    """Copy what the session recorded to this computer, so that it can be read after it stops."""
    target = folder(record["id"])
    notes = []
    try:
        (target / "heartbeats.json").write_text(json.dumps(_heartbeats(record)), encoding="utf-8")
    except OSError as error:
        notes.append(f"The activity record could not be read: {error}")
    consent = compose(record, "exec", "-T", "edge", "cat", "/var/log/gremlin/consent.log", timeout=60)
    (target / "consent.log").write_text(consent.stdout, encoding="utf-8")
    submissions = target / "submissions"
    submissions.mkdir(exist_ok=True)
    for name, text in _live_submissions(record).items():
        (submissions / name).write_text(text, encoding="utf-8", newline="\n")
    return notes


def delete(sid):
    update(sid, status="deleting", error="")
    return in_background(sid, _delete)


def _delete(sid):
    record = load(sid)
    result = compose(record, "down", "-v", "--remove-orphans", timeout=240)
    if result.returncode != 0:
        update(sid, status="failed", error="Delete failed: " + tail(result.stderr))
        return
    shutil.rmtree(folder(sid))  # folder() only accepts a well-formed session id


def recover():
    """After the app restarts, settle sessions that were in the middle of something."""
    for record in all_sessions():
        if record["status"] in ("starting", "ending", "deleting"):
            alive = editor_running(record["id"]) and editor_answers(record) and record.get("key")
            update(record["id"], status="ready" if alive else "failed",
                   error="" if alive else "The interviewer app stopped while this was in progress.")


# ---------------------------------------------------------------- reading a session


def _heartbeats(record):
    if not record.get("key"):
        return []
    auth = "Basic " + base64.b64encode(record["key"].encode()).decode()
    day = datetime.datetime.fromisoformat(record["created"]).date()
    today = datetime.datetime.now(datetime.timezone.utc).date()
    events = []
    while day <= today:
        request = urllib.request.Request(
            f"http://127.0.0.1:{record['wakapi_port']}/api/compat/wakatime/v1/users/current/heartbeats?date={day}",
            headers={"Authorization": auth})
        with _http.open(request, timeout=10) as response:
            events += json.load(response).get("data", [])
        day += datetime.timedelta(days=1)
    return sorted(events, key=lambda e: e["time"])


def _live_submissions(record):
    listing = compose(record, "exec", "-T", "collector", "ls", f"/submissions/{record['id']}", timeout=60)
    found = {}
    if listing.returncode != 0:  # nothing submitted yet
        return found
    for name in listing.stdout.split():
        if SUBMISSION_NAME.fullmatch(name):
            text = compose(record, "exec", "-T", "collector", "cat", f"/submissions/{record['id']}/{name}", timeout=60)
            found[name] = text.stdout
    return found


def read(record):
    """Everything recorded for a session: events, consent entries and submissions."""
    sid = record["id"]
    data = {"events": [], "consent": [], "submissions": {}, "running": False, "problem": ""}
    consent_text = ""
    if record["status"] == "ready":
        cached = _cache.get(sid)
        if cached and time.time() - cached[0] < 5:
            return cached[1]
        data["running"] = editor_running(sid)
        if data["running"]:
            try:
                data["events"] = _heartbeats(record)
            except OSError as error:
                data["problem"] = f"The activity record could not be read just now: {error}"
            consent_text = compose(record, "exec", "-T", "edge", "cat", "/var/log/gremlin/consent.log",
                                   timeout=60).stdout
            data["submissions"] = _live_submissions(record)
    else:
        target = folder(sid)
        if (target / "heartbeats.json").is_file():
            data["events"] = json.loads((target / "heartbeats.json").read_text(encoding="utf-8"))
        if (target / "consent.log").is_file():
            consent_text = (target / "consent.log").read_text(encoding="utf-8")
        if (target / "submissions").is_dir():
            data["submissions"] = {p.name: p.read_text(encoding="utf-8", errors="replace")
                                   for p in sorted((target / "submissions").iterdir())
                                   if SUBMISSION_NAME.fullmatch(p.name)}
    for line in consent_text.splitlines():
        try:
            entry = json.loads(line)
            uri = entry["request"]["uri"]
            version = re.search(r"[?&]v=([0-9]+)", uri)
            data["consent"].append({"time": entry["ts"], "version": version.group(1) if version else "?"})
        except (ValueError, KeyError, TypeError):
            continue
    if record["status"] == "ready":
        _cache[sid] = (time.time(), data)
    return data
