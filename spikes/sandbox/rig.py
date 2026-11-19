"""Spike rig: a sandbox that can reach only the gateway, with a local model behind it.

Throwaway. Spec 11 says spike code is never promoted without a rewrite.

What it builds on the machine's Docker:

    sandbox container --(internal network, no route out)--> gateway container --> model API

The gateway is spikes/gateway/gateway.py. The model API is a local Ollama on the host, so there
is no key and no cost. A local model is far weaker than the pinned production model. It shows
that the plumbing works and what an agent sends. It says nothing about how hard a problem is.

    uv run python spikes/sandbox/rig.py wiring
    uv run python spikes/sandbox/rig.py down
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "spikes" / "gateway"
RUNS_DIR = ROOT / "pilot" / "spikes" / "sandbox"  # pilot/ is not in git

LABEL = "gassess-spike=1"
NET_OUT = "gassess-spike-out"  # has a route out. Only the gateway is on it
NET_SBX = "gassess-spike-sbx"  # internal: no route out. Sandboxes and the gateway are on it
GW = "gassess-spike-gw"
GW_ALIAS = "gw"
GW_PORT = 8080
PY_IMAGE = "python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"
OLLAMA_FROM_CONTAINER = "http://host.docker.internal:11434"
REAL_KEY = "not-a-real-key-ollama-ignores-it"  # what the gateway sends upstream. Must never reach a log

BASE_MODEL = "qwen3:4b-instruct"
MODEL = "gassess-qwen3-4b-16k"  # the pinned model: BASE_MODEL with a 16k context
SMALL_MODEL = "qwen2.5:1.5b"
CONTEXT = 16384


class RigError(RuntimeError):
    pass


def docker(*args: str, check: bool = True, timeout: float | None = 180, stdin: str | None = None):
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            input=stdin, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RigError(f"docker {' '.join(args[:3])} timed out after {timeout}s") from exc
    if check and result.returncode != 0:
        raise RigError(f"docker {' '.join(args[:4])} failed ({result.returncode}): {(result.stderr or result.stdout).strip()}")
    return result


@dataclass
class Sandbox:
    name: str
    ip: str


@dataclass
class Rig:
    run_dir: Path
    image: str
    sandboxes: dict[str, Sandbox] = field(default_factory=dict)

    # --- life cycle ------------------------------------------------------------------

    def down(self, *, keep_logs: bool = True) -> None:
        if keep_logs and self._exists(GW):
            dest = self.run_dir / "gateway"
            dest.mkdir(parents=True, exist_ok=True)
            docker("cp", f"{GW}:/data/.", str(dest), check=False)
        ids = docker("ps", "-aq", "--filter", f"label={LABEL}", check=False).stdout.split()
        if ids:
            docker("rm", "-f", *ids, check=False)
        for net in (NET_SBX, NET_OUT):
            docker("network", "rm", net, check=False)
        self.sandboxes.clear()

    @staticmethod
    def _exists(name: str) -> bool:
        return docker("inspect", "-f", "{{.Id}}", name, check=False).returncode == 0

    def up(self) -> None:
        self.down(keep_logs=False)
        docker("network", "create", "--label", LABEL, NET_OUT)
        docker("network", "create", "--internal", "--label", LABEL, NET_SBX)
        docker(
            "run", "-d", "--name", GW, "--label", LABEL, "--network", NET_OUT,
            "--mount", f"type=bind,source={GATEWAY_DIR},target=/gw,readonly",
            "-e", f"ANTHROPIC_API_KEY={REAL_KEY}",
            PY_IMAGE, "sh", "-c",
            f"mkdir -p /data/logs && exec python /gw/gateway.py serve --upstream {OLLAMA_FROM_CONTAINER} "
            f"--sessions /data/sessions.json --log-dir /data/logs --host 0.0.0.0 --port {GW_PORT}",
        )
        docker("network", "connect", "--alias", GW_ALIAS, NET_SBX, GW)
        deadline = time.time() + 30
        while time.time() < deadline:
            probe = docker(
                "exec", GW, "python", "-c",
                f"import socket; socket.create_connection(('127.0.0.1', {GW_PORT}), 2)", check=False,
            )
            if probe.returncode == 0:
                return
            time.sleep(0.5)
        raise RigError("the gateway did not start: " + docker("logs", GW, check=False).stdout[-600:])

    def start_sandbox(self, name: str, *, workspace: Path | None = None, config: Path | None = None,
                      read_only: bool = True) -> Sandbox:
        args = [
            "run", "-d", "--name", name, "--label", LABEL, "--network", NET_SBX,
            "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        ]
        if read_only:
            args += [
                "--read-only",
                "--tmpfs", "/tmp:rw,exec,size=512m",
                "--tmpfs", "/home/candidate:rw,exec,size=1g,uid=1000,gid=1000,mode=0755",
            ]
        if workspace is not None:
            args += ["--mount", f"type=bind,source={workspace},target=/workspace"]
        if config is not None:
            args += ["--mount", f"type=bind,source={config},target=/etc/opencode/opencode.json,readonly"]
        docker(*args, self.image, "sleep", "infinity")
        ip = docker(
            "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{NET_SBX}").IPAddress}}}}', name
        ).stdout.strip()
        sandbox = Sandbox(name, ip)
        self.sandboxes[name] = sandbox
        return sandbox

    # --- gateway sessions ---------------------------------------------------------------

    def new_session(self, session_id: str, *, models: list[str], bind: list[str], minutes: float = 60,
                    max_requests: int | None = None) -> str:
        cmd = ["exec", GW, "python", "/gw/gateway.py", "session", "add", "--sessions", "/data/sessions.json",
               "--id", session_id, "--minutes", str(minutes)]
        for model in models:
            cmd += ["--model", model]
        for address in bind:
            cmd += ["--bind", address]
        if max_requests is not None:
            cmd += ["--max-requests", str(max_requests)]
        lines = [line.strip() for line in docker(*cmd).stdout.splitlines() if line.strip()]
        return lines[-1]

    def revoke_session(self, session_id: str) -> None:
        docker("exec", GW, "python", "/gw/gateway.py", "session", "revoke", "--sessions", "/data/sessions.json",
               "--id", session_id)

    def gateway_log(self, session_id: str) -> list[dict]:
        out = docker("exec", GW, "sh", "-c", f"cat /data/logs/{session_id}.jsonl 2>/dev/null || true").stdout
        records = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    records.append({"unparsed": line[:200]})
        return records

    def all_log_text(self) -> str:
        return docker("exec", GW, "sh", "-c", "cat /data/logs/*.jsonl 2>/dev/null || true").stdout

    # --- running things in a sandbox ----------------------------------------------------

    def sh(self, name: str, script: str, *, env: dict[str, str] | None = None, timeout: float = 120,
           workdir: str = "/workspace", user: str = "candidate"):
        args = ["exec", "-i", "-u", user, "-w", workdir]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        return docker(*args, name, "sh", "-c", script, check=False, timeout=timeout)

    def call(self, name: str, *, token: str | None, body: dict, path: str = "/v1/messages",
             timeout: float = 300) -> dict:
        """One HTTP call to the gateway, made from inside a sandbox with the standard library."""
        spec = json.dumps({"host": GW_ALIAS, "port": GW_PORT, "token": token, "body": body, "path": path,
                           "timeout": timeout})
        result = docker("exec", "-i", "-u", "candidate", name, "python", "-c", CLIENT, spec, check=False,
                        timeout=timeout + 30)
        if result.returncode != 0:
            return {"error": (result.stderr or result.stdout).strip()[-400:]}
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            return {"error": "unreadable client output: " + result.stdout[-300:]}


# What runs inside a sandbox to make one call. It records when each piece of the response arrived,
# which is how the checks tell a streamed reply from a buffered one.
CLIENT = r"""
import http.client, json, sys, time
a = json.loads(sys.argv[1])
t0 = time.time()
headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
if a.get("token"):
    headers["x-api-key"] = a["token"]
out = {}
try:
    conn = http.client.HTTPConnection(a["host"], a["port"], timeout=a["timeout"])
    conn.request("POST", a["path"], body=json.dumps(a["body"]).encode(), headers=headers)
    r = conn.getresponse()
    out["status"] = r.status
    out["content_type"] = r.getheader("content-type")
    pieces, text = [], b""
    while True:
        chunk = r.read1(65536)
        if not chunk:
            break
        pieces.append(round(time.time() - t0, 3))
        text += chunk
    out["first_byte_s"] = pieces[0] if pieces else None
    out["last_byte_s"] = pieces[-1] if pieces else None
    out["chunks"] = len(pieces)
    out["body"] = text.decode("utf-8", "replace")[:30000]
except Exception as exc:
    out["error"] = type(exc).__name__ + ": " + str(exc)
print(json.dumps(out))
"""


# --- helpers for the checks --------------------------------------------------------------


@dataclass
class Check:
    id: str
    name: str
    ok: bool
    evidence: str


def say(text: str = "") -> None:
    print(text, flush=True)


def ensure_model() -> None:
    """Make the pinned model: the small instruct model with a 16k context window."""
    have = subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout
    if MODEL in have:
        return
    modelfile = RUNS_DIR / "Modelfile"
    modelfile.parent.mkdir(parents=True, exist_ok=True)
    modelfile.write_text(f"FROM {BASE_MODEL}\nPARAMETER num_ctx {CONTEXT}\n", encoding="utf-8")
    subprocess.run(["ollama", "create", MODEL, "-f", str(modelfile)], check=True, capture_output=True)


def new_run_dir(label: str) -> Path:
    path = RUNS_DIR / f"{label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def short(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 3] + "..."


def msg(model: str, content: str, *, max_tokens: int = 24, **extra) -> dict:
    return {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}], **extra}


WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Get the current weather for a city.",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}


# --- the wiring checks ---------------------------------------------------------------------


def run_wiring(image: str) -> int:
    ensure_model()
    run_dir = new_run_dir("wiring")
    rig = Rig(run_dir, image)
    checks: list[Check] = []

    def record(check_id: str, name: str, ok: bool, evidence: str) -> None:
        checks.append(Check(check_id, name, ok, evidence))
        say(f"  [{'PASS' if ok else 'FAIL'}] {check_id} {name}: {short(evidence)}")

    try:
        say("Bringing up the networks and the gateway ...")
        rig.up()
        s1 = rig.start_sandbox("gassess-spike-sbx1")
        s2 = rig.start_sandbox("gassess-spike-sbx2")
        say(f"  sandboxes at {s1.ip} and {s2.ip}")
        token = rig.new_session("S-WIRE", models=[MODEL], bind=[s1.ip], minutes=30)

        say("Network:")
        r = rig.sh(s1.name, "curl -s -m 6 -o /dev/null -w '%{http_code}' https://example.com; echo \" exit=$?\"")
        record("N1", "no route to the internet by name", "exit=0" not in r.stdout, r.stdout.strip() or r.stderr.strip())
        r = rig.sh(s1.name, "curl -s -m 6 -o /dev/null -w '%{http_code}' https://1.1.1.1; echo \" exit=$?\"")
        record("N2", "no route to the internet by address", "exit=0" not in r.stdout, r.stdout.strip() or r.stderr.strip())
        r = rig.sh(s1.name, "curl -s -m 6 http://host.docker.internal:11434/api/version; echo \" exit=$?\"")
        record("N3", "the model server on the host is not reachable directly", "version" not in r.stdout,
               r.stdout.strip() or r.stderr.strip())
        r = rig.sh(s1.name, f"curl -s -m 6 -o /dev/null -w '%{{http_code}}' http://{GW_ALIAS}:{GW_PORT}/nothing; echo")
        record("N4", "the gateway is reachable by name", r.stdout.strip().startswith("404"), r.stdout.strip())

        say("Gateway and a real local model:")
        t0 = time.time()
        reply = rig.call(s1.name, token=token, body=msg(MODEL, "Reply with the single word: ready"))
        text = reply.get("body", "")
        record("G1", "a call through the gateway reaches the local model",
               reply.get("status") == 200 and '"text"' in text and MODEL in text,
               f"status {reply.get('status')} in {time.time() - t0:.1f}s: {short(text, 140)}")

        streamed = rig.call(
            s1.name, token=token,
            body=msg(MODEL, "Count from 1 to 40 separated by commas, nothing else.", max_tokens=160, stream=True),
        )
        first, last, chunks = streamed.get("first_byte_s"), streamed.get("last_byte_s"), streamed.get("chunks", 0)
        record("G2", "a streamed reply arrives in pieces, not all at the end",
               streamed.get("status") == 200 and chunks > 3 and first is not None and last - first > 0.5
               and "message_start" in streamed.get("body", "") and "message_stop" in streamed.get("body", ""),
               f"{chunks} chunks, first byte {first}s, last {last}s, event stream {streamed.get('content_type')}")

        tooled = rig.call(
            s1.name, token=token,
            body=msg(MODEL, "What is the weather in Paris? Use the get_weather tool.", max_tokens=300,
                     tools=[WEATHER_TOOL]),
        )
        try:
            parsed = json.loads(tooled.get("body", "{}"))
        except json.JSONDecodeError:
            parsed = {}
        blocks = [b for b in parsed.get("content", []) if b.get("type") == "tool_use"]
        record("G3", "a tool call comes back through the gateway unchanged",
               bool(blocks) and parsed.get("stop_reason") == "tool_use",
               f"stop_reason {parsed.get('stop_reason')}, tool_use {json.dumps(blocks[0])[:120] if blocks else 'none'}")
        if blocks:
            follow = rig.call(
                s1.name, token=token,
                body={"model": MODEL, "max_tokens": 120, "tools": [WEATHER_TOOL], "messages": [
                    {"role": "user", "content": "What is the weather in Paris? Use the get_weather tool."},
                    {"role": "assistant", "content": parsed["content"]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": blocks[0]["id"],
                                                  "content": "18 degrees and sunny"}]},
                ]},
            )
            record("G4", "the tool result goes back and the model answers",
                   follow.get("status") == 200 and "18" in follow.get("body", ""), short(follow.get("body", ""), 140))

        say("Rules the gateway enforces:")
        r = rig.call(s1.name, token=token, body=msg("claude-opus-5-5", "hi"))
        record("P1", "a model that is not pinned is refused", r.get("status") == 400, f"{r.get('status')} {short(r.get('body',''), 120)}")
        r = rig.call(s1.name, token=token, body=msg(MODEL, "hi", tools=[{"type": "web_search_20250305", "name": "web_search"}]))
        record("P2", "a server-side web tool is refused", r.get("status") == 400, f"{r.get('status')} {short(r.get('body',''), 120)}")
        r = rig.call(s1.name, token="not-the-token", body=msg(MODEL, "hi"))
        record("P3", "a wrong token is refused", r.get("status") in (401, 403), f"{r.get('status')} {short(r.get('body',''), 100)}")
        r = rig.call(s1.name, token=None, body=msg(MODEL, "hi"))
        record("P4", "no token is refused", r.get("status") in (401, 403), f"{r.get('status')} {short(r.get('body',''), 100)}")
        r = rig.call(s2.name, token=token, body=msg(MODEL, "hi"))
        record("P5", "the right token from another sandbox is refused (address binding)",
               r.get("status") in (401, 403), f"{r.get('status')} {short(r.get('body',''), 120)}")
        r = rig.call(s1.name, token=token, body=msg(MODEL, "hi"), path="/v1/models")
        record("P6", "other paths are not proxied", r.get("status") == 404, f"{r.get('status')} {short(r.get('body',''), 100)}")

        cap_token = rig.new_session("S-CAP", models=[SMALL_MODEL], bind=[s1.ip], minutes=30, max_requests=2)
        codes = [rig.call(s1.name, token=cap_token, body=msg(SMALL_MODEL, "Say ok")).get("status") for _ in range(3)]
        record("C1", "the request cap stops the third call", codes[:2] == [200, 200] and codes[2] not in (200, None),
               f"statuses {codes}")

        rev_token = rig.new_session("S-REV", models=[SMALL_MODEL], bind=[s1.ip], minutes=30)
        before = rig.call(s1.name, token=rev_token, body=msg(SMALL_MODEL, "Say ok")).get("status")
        rig.revoke_session("S-REV")
        after = rig.call(s1.name, token=rev_token, body=msg(SMALL_MODEL, "Say ok")).get("status")
        record("R1", "a revoked token stops working at once", before == 200 and after in (401, 403), f"before {before}, after {after}")

        say("Logs:")
        records = rig.gateway_log("S-WIRE")
        everything = rig.all_log_text()
        record("L1", "every call is in the log, requests and responses",
               len(records) >= 4, f"{len(records)} records for S-WIRE; keys {sorted(records[0].keys()) if records else '-'}")
        record("L2", "neither the token nor the upstream key is ever written to a log",
               token not in everything and cap_token not in everything and REAL_KEY not in everything,
               "searched all log files")
    except RigError as exc:
        record("X", "the rig itself", False, str(exc))
    finally:
        say("Tearing down ...")
        try:
            rig.down()
        except RigError as exc:
            say(f"  teardown problem: {exc}")

    (run_dir / "results.json").write_text(
        json.dumps([c.__dict__ for c in checks], indent=2), encoding="utf-8"
    )
    failed = [c for c in checks if not c.ok]
    say("")
    say(f"{len(checks) - len(failed)} of {len(checks)} checks passed. Results and gateway logs are in {run_dir}")
    return 1 if failed else 0


# --- the agent hands-on ---------------------------------------------------------------------

TOY = ROOT / "tests" / "fixtures" / "packages" / "toy"
AGENT_NAME = "gassess-spike-agent"
AGENT_PROMPT = "Complete the tasks in BRIEF.md. Record your findings in FINDINGS.yaml."  # the V7 prompt
GATEWAY_BASE_URL = f"http://{GW_ALIAS}:{GW_PORT}/v1"  # an agent built on the AI SDK wants /v1 on the end

# Set by the launcher. A candidate can unset them, so they are comfort and not the lock.
AGENT_ENV = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_FILEWATCHER": "1",
}


def locked_config(model: str = MODEL, *, custom_model: bool = True) -> dict:
    """The config the candidate cannot edit: our gateway, the pinned model, nothing that reaches out.

    With `custom_model` the model is declared here, which a local model needs. Without it the model
    must already be in OpenCode's own catalog, which is how the real Claude model ids are set up."""
    provider: dict = {"settings": {"baseURL": GATEWAY_BASE_URL, "apiKey": "{env:ANTHROPIC_API_KEY}"}}
    if custom_model:
        provider["models"] = {model: {"name": model, "limit": {"context": CONTEXT, "output": 4096}}}
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"anthropic/{model}",
        "update": "disable",
        "share": "disabled",
        "websearch": False,
        "enabled_providers": ["anthropic"],
        "providers": {"anthropic": provider},
        "permissions": [
            {"action": "webfetch", "resource": "*", "effect": "deny"},
            {"action": "websearch", "resource": "*", "effect": "deny"},
        ],
    }


def export_workspace(dest: Path) -> Path:
    from gremlin_assess.cli import main as gassess
    from gremlin_assess.container import open_permissions

    if gassess(["export", str(TOY), str(dest)]) != 0:
        raise RigError("the workspace could not be exported")
    open_permissions(dest)
    return dest


def pull_dir(rig: Rig, name: str, path: str, dest: Path) -> bool:
    """Copy a folder out of a sandbox. `docker cp` cannot read a tmpfs, so this streams a tar."""
    import io
    import tarfile

    parent, leaf = path.rsplit("/", 1)
    proc = subprocess.run(["docker", "exec", "-u", "candidate", name, "tar", "-cf", "-", "-C", parent, leaf],
                          capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return False
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return True


def as_object(value) -> dict:
    """A log field that may be an object or a string holding JSON."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def summarise_log(records: list[dict]) -> list[dict]:
    """One row per request: what the agent asked for and what came back."""
    requests = {r["id"]: r for r in records if r.get("event") == "request"}
    responses = {r["id"]: r for r in records if r.get("event") == "response"}
    rows = []
    for rid in sorted(requests):
        rq, rs = requests[rid], responses.get(rid, {})
        body = as_object(rq.get("body"))
        headers = as_object(rq.get("headers"))
        usage = as_object(rs.get("usage"))
        tools = [t.get("name") or t.get("type") for t in body.get("tools", []) if isinstance(t, dict)]
        first_user = ""
        for message in body.get("messages", []):
            if message.get("role") == "user":
                content = message.get("content")
                first_user = content if isinstance(content, str) else json.dumps(content)
                break
        rows.append({
            "id": rid, "time": rq.get("time"), "path": rq.get("path") + (f"?{rq['query']}" if rq.get("query") else ""),
            "model": rq.get("model"), "stream": rq.get("stream"), "status": rs.get("status"),
            "input_tokens": rs.get("usage_input_total"), "output_tokens": usage.get("output_tokens"),
            "stop_reason": rs.get("stop_reason"), "ms": rs.get("duration_ms"), "user_agent": rq.get("user_agent"),
            "flags": (rq.get("flags") or []) + (rs.get("flags") or []), "tools": tools,
            "system_chars": len(json.dumps(body.get("system"))) if body.get("system") else 0,
            "messages": len(body.get("messages", [])), "max_tokens": body.get("max_tokens"),
            "thinking": body.get("thinking"), "anthropic_beta": headers.get("anthropic-beta"),
            "first_user": first_user[:140], "body_keys": sorted(body.keys()),
            "cache_control_blocks": json.dumps(body).count('"cache_control"'),
            "temperature": body.get("temperature"), "tool_choice": body.get("tool_choice"),
            "output_config": body.get("output_config"),
        })
    return rows


def print_details(rows: list[dict], limit: int = 8) -> None:
    """What each distinct kind of request looked like: the first of each (tool count, system size) pair."""
    seen = set()
    shown = 0
    for r in rows:
        key = (len(r["tools"]), r["system_chars"] // 50, r["model"])
        if key in seen or shown >= limit:
            continue
        seen.add(key)
        shown += 1
        say(f"  --- request {r['id']}: {r['path']}")
        say(f"      user agent    {r['user_agent']}")
        say(f"      beta header   {r['anthropic_beta']}")
        say(f"      body keys     {r['body_keys']}")
        say(f"      thinking {r['thinking']} | output_config {r['output_config']} | temperature {r['temperature']} "
            f"| tool_choice {r['tool_choice']} | max_tokens {r['max_tokens']} | cache_control blocks {r['cache_control_blocks']}")
        say(f"      system chars  {r['system_chars']} | tools ({len(r['tools'])}) {r['tools']}")
        say(f"      first message {r['first_user']}")


def print_rows(rows: list[dict]) -> None:
    say(f"  {'id':>3} {'model':<24} {'strm':<5} {'stat':<4} {'in':>6} {'out':>5} {'stop':<10} {'tools':>5} {'msgs':>4} {'ms':>7}")
    for r in rows:
        say(f"  {r['id']:>3} {str(r['model'])[:24]:<24} {str(r['stream']):<5} {str(r['status']):<4} "
            f"{str(r['input_tokens']):>6} {str(r['output_tokens']):>5} {str(r['stop_reason']):<10} "
            f"{len(r['tools']):>5} {r['messages']:>4} {str(r['ms']):>7}")


@dataclass
class AgentRun:
    rig: Rig
    run_dir: Path
    sandbox: Sandbox
    token: str
    session: str

    def cli(self, label: str, script: str, *, env: dict[str, str] | None = None, timeout: float = 600,
            workdir: str = "/workspace") -> dict:
        merged = {"ANTHROPIC_API_KEY": self.token, **AGENT_ENV, **(env or {})}
        started = time.time()
        result = self.rig.sh(self.sandbox.name, script, env=merged, timeout=timeout + 30, workdir=workdir)
        took = round(time.time() - started, 1)
        (self.run_dir / f"{label}.out").write_text(result.stdout, encoding="utf-8")
        (self.run_dir / f"{label}.err").write_text(result.stderr, encoding="utf-8")
        say(f"  {label}: exit {result.returncode} in {took}s ({len(result.stdout)} bytes out, {len(result.stderr)} bytes err)")
        return {"label": label, "exit": result.returncode, "seconds": took, "stdout": result.stdout, "stderr": result.stderr}

    def rows(self) -> list[dict]:
        return summarise_log(self.rig.gateway_log(self.session))


def start_agent_run(image: str, kind: str, *, locked: bool = True, minutes: int = 60, max_requests: int = 400,
                    label: str | None = None, model: str = MODEL, custom_model: bool = True) -> AgentRun:
    ensure_model()
    run_dir = new_run_dir(label or f"agent-{kind}")
    workspace = export_workspace(run_dir / "workspace")
    config_path = None
    if locked:
        config_path = run_dir / "config" / "opencode.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(locked_config(model, custom_model=custom_model), indent=2), encoding="utf-8")
    rig = Rig(run_dir, image)
    rig.up()
    sandbox = rig.start_sandbox(AGENT_NAME, workspace=workspace, config=config_path)
    token = rig.new_session("S-AGENT", models=[model], bind=[sandbox.ip], minutes=minutes, max_requests=max_requests)
    return AgentRun(rig, run_dir, sandbox, token, "S-AGENT")


def export_sessions(run: AgentRun) -> None:
    """Save every session the agent kept, in the form its own export command gives."""
    listing = run.cli("session-list", "timeout 60 opencode session list --standalone")["stdout"]
    ids = [line.split()[0] for line in listing.splitlines() if line.startswith("ses_")]
    for index, session_id in enumerate(ids):
        run.cli(f"session-export-{index}", f"timeout 90 opencode session export {session_id} --standalone", timeout=120)


def agent_prod_shape(image: str) -> int:
    """The production-shaped config. The real model id is a catalog model, and a local alias answers for it."""
    alias = "claude-opus-5-5"
    ensure_model()
    if alias in subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout:
        raise RigError(f"{alias} already exists in Ollama. Not touching it")
    subprocess.run(["ollama", "cp", MODEL, alias], check=True, capture_output=True)
    try:
        run = start_agent_run(image, "prodshape", model=alias, custom_model=False)
        try:
            run.cli("config-sources", "opencode debug config")
            run.cli("tiny", f"timeout 300 opencode run --standalone --auto --format json "
                            f"--model anthropic/{alias} 'Reply with the single word: ready'", timeout=330)
            export_sessions(run)
            rows = run.rows()
            say(f"What the gateway saw ({len(rows)} requests):")
            print_rows(rows)
            print_details(rows)
        finally:
            finish_agent_run(run)
        say(f"Everything is in {run.run_dir}")
    finally:
        subprocess.run(["ollama", "rm", alias], capture_output=True)  # the alias was only for this test
    return 0


def finish_agent_run(run: AgentRun) -> None:
    for remote, local in (("/home/candidate/.local/share/opencode", "data"), ("/home/candidate/.config/opencode", "config-home")):
        pull_dir(run.rig, run.sandbox.name, remote, run.run_dir / local)
    try:
        diff = run.rig.sh(run.sandbox.name,
                          "git -C /workspace/tallybook diff; git -C /workspace/tallybook status --short", timeout=60)
        (run.run_dir / "workspace.diff").write_text(diff.stdout, encoding="utf-8")
        rows = run.rows()
        (run.run_dir / "requests.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    finally:
        run.rig.down()  # always, so a bug in the summary never leaves containers behind


def agent_probe(image: str) -> int:
    """Startup with no route out, what each OpenCode command shows, and one tiny prompt."""
    run = start_agent_run(image, "probe")
    try:
        say("Startup and what the CLI shows (no route out):")
        run.cli("version", "opencode --version")
        run.cli("paths", "opencode debug paths")
        run.cli("config-sources", "opencode debug config")
        run.cli("agents", "timeout 60 opencode debug agents --standalone")
        run.cli("models", "timeout 90 opencode models --standalone", timeout=120)
        say("A tiny prompt through the gateway:")
        run.cli("tiny", f"timeout 300 opencode run --standalone --auto --format json "
                        f"--model anthropic/{MODEL} 'Reply with the single word: ready'", timeout=330)
        export_sessions(run)
        rows = run.rows()
        say(f"What the gateway saw ({len(rows)} requests):")
        print_rows(rows)
        print_details(rows)
    finally:
        finish_agent_run(run)
    say(f"Everything is in {run.run_dir}")
    return 0


def agent_override(image: str) -> int:
    """Can candidate-owned config beat the locked one? A control without the lock shows the attempts work."""
    outcomes = {}
    for locked in (True, False):
        label = "locked" if locked else "control"
        say(f"--- {label}: managed config {'mounted' if locked else 'absent'}, candidate-owned overrides in place")
        run = start_agent_run(image, f"override-{label}", locked=locked, minutes=20, max_requests=40)
        try:
            run.rig.sh(run.sandbox.name,
                       "mkdir -p ~/.config/opencode && cat > ~/.config/opencode/opencode.json <<'EOF'\n"
                       '{"model": "anthropic/user-config-model", "share": "auto", "update": "auto"}\nEOF\n'
                       "cat > /workspace/opencode.json <<'EOF'\n"
                       '{"model": "anthropic/project-config-model"}\nEOF\n'
                       "cat > /tmp/env-config.json <<'EOF'\n"
                       '{"model": "anthropic/env-file-model"}\nEOF\n', timeout=30)
            env = {"OPENCODE_CONFIG": "/tmp/env-config.json",
                   "OPENCODE_CONFIG_CONTENT": '{"model": "anthropic/env-content-model"}'}
            run.cli("sources", "opencode debug config", env=env)
            run.cli("tiny", "timeout 240 opencode run --standalone --auto --format json "
                            "'Reply with the single word: ready'", env=env, timeout=270)
            rows = run.rows()
            outcomes[label] = sorted({str(r["model"]) for r in rows})
            say(f"  models the gateway saw ({label}): {outcomes[label]}")
            print_rows(rows)
        finally:
            finish_agent_run(run)
    say("")
    say(f"locked:  {outcomes.get('locked')}")
    say(f"control: {outcomes.get('control')}")
    return 0


def agent_task(image: str, minutes: int) -> int:
    """The V7-style run: the agent alone, given only the standard prompt, with a time limit."""
    run = start_agent_run(image, "task", minutes=minutes + 10, max_requests=500)
    try:
        say(f"Running the agent for up to {minutes} minutes with the V7 prompt ...")
        result = run.cli("task", f"timeout {minutes * 60} opencode run --standalone --auto --format json "
                                 f"--model anthropic/{MODEL} '{AGENT_PROMPT}'", timeout=minutes * 60 + 60)
        say(f"The command ended with exit {result['exit']} (124 means the time limit)")
        export_sessions(run)
        tests = run.rig.sh(run.sandbox.name, "cd /workspace/tallybook && python -m pytest -q -p no:cacheprovider 2>&1 | tail -15",
                           timeout=180)
        (run.run_dir / "tests-after.out").write_text(tests.stdout, encoding="utf-8")
        say("Public tests after the run:")
        say(tests.stdout.rstrip())
        rows = run.rows()
        say(f"What the gateway saw ({len(rows)} requests):")
        print_rows(rows)
        print_details(rows, limit=4)
    finally:
        finish_agent_run(run)
    say(f"Everything is in {run.run_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spike rig for the sandbox network and agent tests")
    commands = parser.add_subparsers(dest="command", required=True)
    wiring = commands.add_parser("wiring", help="check the network wiring and the gateway against the local model")
    wiring.add_argument("--image", default="gassess-spike-sbx:oc-2.3.2")
    agent = commands.add_parser("agent", help="OpenCode in the sandbox, through the gateway, to the local model")
    agent.add_argument("experiment", choices=["probe", "override", "task", "prod-shape"])
    agent.add_argument("--image", default="gassess-spike-sbx:oc-2.3.2")
    agent.add_argument("--minutes", type=int, default=15, help="time limit for the task experiment")
    commands.add_parser("down", help="remove every container and network this rig made")
    args = parser.parse_args(argv)

    if args.command == "down":
        Rig(new_run_dir("down"), "unused").down(keep_logs=False)
        return 0
    if args.command == "agent":
        if args.experiment == "probe":
            return agent_probe(args.image)
        if args.experiment == "override":
            return agent_override(args.image)
        if args.experiment == "prod-shape":
            return agent_prod_shape(args.image)
        return agent_task(args.image, args.minutes)
    return run_wiring(args.image)


if __name__ == "__main__":
    sys.exit(main())
