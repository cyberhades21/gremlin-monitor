"""A thin model gateway. Spike code: see docs/spikes/05-gateway.md and docs/specs/11.

It sits between a candidate's agent and the model API. It never changes a
request body or a response. It does these things:

- swaps the session token for the real API key, so no real key is in a sandbox
- serves only the messages endpoints, and refuses everything else
- refuses any model other than the ones pinned for the session
- refuses server-side tools that reach outside (web search, web fetch, code execution, MCP connector)
- counts tokens against a cap for the session, and refuses once it is used up
- binds a token to the sandbox's address, so a copied token fails from elsewhere
- flags requests that do not look like the agent (the fingerprint)
- writes every request and response, in full, to a log per session

Standard library only. Per spec 11 it is thrown away and rewritten before it
becomes part of the platform.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import ssl
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MESSAGES = "/v1/messages"
COUNT_TOKENS = "/v1/messages/count_tokens"
HELLO = "/api/hello"  # the agent's connection-warming probe
SERVED = {("POST", MESSAGES), ("POST", COUNT_TOKENS)}

# Tool types that run on the provider's side and can reach outside the sandbox.
# Tools the agent runs itself (bash, text editor, custom tools) are allowed.
SERVER_TOOL_PREFIXES = ("web_search", "web_fetch", "code_execution")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
}
# Request headers that are never passed on: the token and the real key are swapped,
# and the rest are either hop-by-hop or would tell the provider who is behind us.
NOT_FORWARDED = HOP_BY_HOP | {
    "host", "content-length", "expect", "x-api-key", "authorization", "accept-encoding",
    "cookie", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
}
LOGGED_REQUEST_HEADERS = ("anthropic-version", "anthropic-beta", "content-type")
LOGGED_RESPONSE_HEADERS = ("request-id", "retry-after", "x-should-retry", "content-type")

MAX_BODY_BYTES = 64 * 1024 * 1024
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class BodyTooLarge(Exception):
    pass


class BadBody(Exception):
    pass


@dataclass
class Session:
    id: str
    token_sha256: str
    models: list[str]
    max_input_tokens: int
    max_output_tokens: int
    max_requests: int
    bind: list[str]  # client addresses allowed. Empty means any, for development only
    expires_at: float | None = None
    revoked: bool = False
    user_agent_prefix: str | None = None  # the agent's fingerprint. A mismatch is flagged, not refused


@dataclass
class Usage:
    input_tokens: int = 0  # includes cache writes and cache reads
    output_tokens: int = 0
    requests: int = 0


# --- sessions file ---------------------------------------------------------------


def normalise_address(text: str) -> str:
    """The address in one form. An IPv4 address written as IPv6 ('::ffff:1.2.3.4') becomes plain IPv4."""
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return text
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped or address)


def load_sessions(path: Path) -> dict[str, Session]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_bytes().decode("utf-8"))
    sessions = {}
    for entry in data.get("sessions", []):
        session = Session(**entry)
        if not SESSION_ID.match(session.id):
            raise ValueError(f"bad session id {session.id!r}")
        sessions[session.id] = session
    return sessions


def save_sessions(path: Path, sessions: dict[str, Session]) -> None:
    """Write atomically, so the running gateway never reads half a file."""
    payload = {"sessions": [asdict(s) for s in sessions.values()]}
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes((json.dumps(payload, indent=2) + "\n").encode("utf-8"))
    os.replace(temp, path)


def add_session(
    path: Path,
    *,
    session_id: str,
    models: list[str],
    max_input_tokens: int,
    max_output_tokens: int,
    max_requests: int,
    bind: list[str],
    minutes: float | None = None,
    user_agent_prefix: str | None = None,
    clock=time.time,
) -> str:
    """Add a session and return its token. Only a hash of the token is stored."""
    if not SESSION_ID.match(session_id):
        raise ValueError("a session id is letters, digits, dot, dash and underscore, and starts with a letter or digit")
    if not models:
        raise ValueError("a session needs at least one pinned model")
    sessions = load_sessions(path)
    if session_id in sessions:
        raise ValueError(f"session {session_id} already exists")
    token = "gw-" + secrets.token_urlsafe(32)
    sessions[session_id] = Session(
        id=session_id,
        token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        models=models,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        max_requests=max_requests,
        bind=[normalise_address(a) for a in bind],
        expires_at=clock() + minutes * 60 if minutes else None,
        user_agent_prefix=user_agent_prefix,
    )
    save_sessions(path, sessions)
    return token


def revoke_session(path: Path, session_id: str) -> None:
    sessions = load_sessions(path)
    if session_id not in sessions:
        raise ValueError(f"no session {session_id}")
    sessions[session_id].revoked = True
    save_sessions(path, sessions)


# --- usage parsing -----------------------------------------------------------------


def _usage_from(raw: dict) -> dict[str, int]:
    return {
        "input_tokens": int(raw.get("input_tokens") or 0),
        "cache_creation_input_tokens": int(raw.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(raw.get("cache_read_input_tokens") or 0),
        "output_tokens": int(raw.get("output_tokens") or 0),
    }


def parse_response(body: bytes, streamed: bool) -> dict:
    """Usage, model and stop reason from a response body. A body that does not parse gives zeros."""
    found: dict = {"usage": _usage_from({}), "model": None, "stop_reason": None}
    text = body.decode("utf-8", errors="replace")
    if not streamed:
        try:
            data = json.loads(text)
        except ValueError:
            return found
        if isinstance(data, dict):
            found["usage"] = _usage_from(data.get("usage") or {})
            found["model"] = data.get("model")
            found["stop_reason"] = data.get("stop_reason")
        return found

    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "message_start":
            message = event.get("message") or {}
            found["model"] = message.get("model")
            found["usage"] = _usage_from(message.get("usage") or {})
        elif event.get("type") == "message_delta":
            raw = event.get("usage") or {}
            # The counts in message_delta are cumulative. Take whatever it carries.
            for key, value in _usage_from(raw).items():
                if key in raw:
                    found["usage"][key] = value
            found["stop_reason"] = (event.get("delta") or {}).get("stop_reason") or found["stop_reason"]
    return found


def input_total(usage: dict[str, int]) -> int:
    return usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]


def _tools(body: dict) -> list[dict]:
    tools = body.get("tools")
    return [t for t in tools if isinstance(t, dict)] if isinstance(tools, list) else []


def tool_types(body: dict) -> list[str]:
    return sorted({str(tool.get("type", "custom")) for tool in _tools(body)})


def server_tools_in(body: dict) -> list[str]:
    found = [kind for kind in tool_types(body) if kind.startswith(SERVER_TOOL_PREFIXES)]
    if body.get("mcp_servers"):
        found.append("mcp_servers")
    return found


# --- the gateway --------------------------------------------------------------------


class Gateway:
    def __init__(
        self,
        upstream: str,
        real_key: str,
        sessions_path: Path,
        log_dir: Path,
        *,
        clock=time.time,
        max_body_bytes: int = MAX_BODY_BYTES,
        upstream_timeout: float = 600.0,
    ):
        parts = urlsplit(upstream)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("the upstream must be an http or https URL")
        self.scheme, self.host = parts.scheme, parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.base_path = parts.path.rstrip("/")
        self._real_key = real_key
        self.sessions_path = Path(sessions_path)
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.max_body_bytes = max_body_bytes
        self.upstream_timeout = upstream_timeout
        self._lock = threading.Lock()
        self._counter = 0
        self._sessions: dict[str, Session] = {}
        self._stamp: tuple | None = None
        self._usage: dict[str, Usage] = {}

    # sessions

    def _reload(self) -> None:
        try:
            info = self.sessions_path.stat()
            stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._sessions = load_sessions(self.sessions_path) if stamp else {}
            self._stamp = stamp

    def authenticate(self, token: str) -> Session | None:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            self._reload()
            found = None
            for session in self._sessions.values():  # compare with all, so timing says nothing about which exists
                if hmac.compare_digest(session.token_sha256, digest):
                    found = session
            return found

    def state_problem(self, session: Session, client: str) -> tuple[int, str, str, str] | None:
        """A reason to refuse before looking at the request: (status, error type, message, flag)."""
        if session.revoked:
            return 401, "authentication_error", "this session has ended", "revoked"
        if session.expires_at is not None and self.clock() >= session.expires_at:
            return 401, "authentication_error", "this session has ended", "expired"
        if session.bind and client not in session.bind:
            return 403, "permission_error", "this token cannot be used from this address", "address_rejected"
        return None

    # usage

    def usage(self, session_id: str) -> Usage:
        with self._lock:
            if session_id not in self._usage:
                self._usage[session_id] = self._read_usage(session_id)
            return self._usage[session_id]

    def _read_usage(self, session_id: str) -> Usage:
        """Add up the counted responses already in the log, so a restart forgets nothing."""
        total = Usage()
        path = self.log_dir / f"{session_id}.jsonl"
        if path.is_file():
            for line in path.read_bytes().decode("utf-8").splitlines():
                record = json.loads(line)
                if record.get("event") == "response" and record.get("counted"):
                    total.input_tokens += record["usage_input_total"]
                    total.output_tokens += record["usage"]["output_tokens"]
                    total.requests += 1
        return total

    def budget_problem(self, session: Session) -> str | None:
        used = self.usage(session.id)
        if used.input_tokens >= session.max_input_tokens:
            return "the input token budget for this session is used up"
        if used.output_tokens >= session.max_output_tokens:
            return "the output token budget for this session is used up"
        if used.requests >= session.max_requests:
            return "the request budget for this session is used up"
        return None

    def add_usage(self, session_id: str, usage: dict[str, int]) -> None:
        total = self.usage(session_id)
        with self._lock:
            total.input_tokens += input_total(usage)
            total.output_tokens += usage["output_tokens"]
            total.requests += 1

    # log

    def next_id(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def log(self, session_id: str | None, record: dict) -> None:
        record = {"time": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="milliseconds"), **record}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with (self.log_dir / f"{session_id or '_unauthenticated'}.jsonl").open("ab") as handle:
                handle.write(line.encode("utf-8"))

    # upstream

    def connect(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(
                self.host, self.port, timeout=self.upstream_timeout, context=ssl.create_default_context()
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=self.upstream_timeout)

    def upstream_headers(self, incoming) -> dict[str, str]:
        headers = {}
        for name, value in incoming.items():
            if name.lower() not in NOT_FORWARDED:
                headers[name] = value
        headers["x-api-key"] = self._real_key
        headers["accept-encoding"] = "identity"  # plain bodies, so they can be logged and counted as they are
        return headers


def read_chunked(rfile, limit: int) -> bytes:
    body = bytearray()
    while True:
        line = rfile.readline(65537)
        try:
            size = int(line.split(b";", 1)[0].strip() or b"0", 16)
        except ValueError as exc:
            raise BadBody("a chunk size is not a number") from exc
        if size == 0:
            while rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                pass
            return bytes(body)
        if len(body) + size > limit:
            raise BodyTooLarge
        body += rfile.read(size)
        rfile.readline(65537)


def make_handler(gateway: Gateway):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # chunked streaming needs it. Every response ends the connection
        server_version = "gateway"
        sys_version = ""
        timeout = 300  # a client that stalls for this long while we read or write is dropped

        def log_message(self, format, *args):  # the gateway's own log replaces the default
            pass

        # every method goes to one place, which refuses the ones it does not serve
        def do_GET(self):
            self._handle()

        do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_GET

        # -- sending

        def _send(self, status: int, body: bytes = b"", content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)

        def _write(self, data: bytes) -> bool:
            """Write and flush to the agent. False when the agent has gone away."""
            try:
                self.wfile.write(data)
                self.wfile.flush()
                return True
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return False

        def _error(self, status: int, error_type: str, message: str) -> None:
            body = json.dumps({"type": "error", "error": {"type": error_type, "message": message}}).encode("utf-8")
            self._send(status, body)

        def _drain(self) -> None:
            """Read a small unread body before replying. Closing a socket with unread data resets the
            connection, and the reset can destroy the reply before the client reads it."""
            if self._body_read:
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if 0 < length <= 1024 * 1024:
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            self._body_read = True

        def _refuse(self, request_id, session, status, error_type, message, reason, flags, request_text=None, **more):
            self._drain()
            record = {
                "event": "rejected",
                "id": request_id,
                "session": session.id if session else None,
                "client": self.client,
                "method": self.command,
                "path": self.path,
                "user_agent": self.headers.get("User-Agent", ""),
                "status": status,
                "reason": reason,
                "flags": flags,
                **more,
            }
            if request_text is not None:
                record["body"] = request_text
            gateway.log(session.id if session else None, record)
            self._error(status, error_type, message)

        # -- reading

        def _token(self) -> str | None:
            key = self.headers.get("x-api-key")
            if key:
                return key.strip()
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return None

        def _read_body(self) -> bytes:
            if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
                body = read_chunked(self.rfile, gateway.max_body_bytes)
                self._body_read = True
                return body
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise BadBody("Content-Length is not a number") from exc
            if length < 0:
                raise BadBody("Content-Length is negative")
            if length > gateway.max_body_bytes:
                raise BodyTooLarge
            body = self.rfile.read(length)
            self._body_read = True
            return body

        # -- the whole request

        def _handle(self) -> None:
            self.close_connection = True
            self._body_read = False
            self.client = normalise_address(self.client_address[0])
            request_id = gateway.next_id()
            path, _, query = self.path.partition("?")

            if self.command == "HEAD" and path == HELLO:
                self._send(200)
                return
            if (self.command, path) not in SERVED:
                self._refuse(
                    request_id, None, 404, "not_found_error", "this gateway only serves the messages endpoints",
                    "route_not_served", ["route_not_served"],
                )
                return

            token = self._token()
            session = gateway.authenticate(token) if token else None
            if session is None:
                prefix = hashlib.sha256((token or "").encode("utf-8")).hexdigest()[:8]
                self._refuse(
                    request_id, None, 401, "authentication_error", "invalid session token",
                    "unknown_token", ["unknown_token"], token_digest=prefix,
                )
                return
            problem = gateway.state_problem(session, self.client)
            if problem:
                status, error_type, message, flag = problem
                self._refuse(request_id, session, status, error_type, message, flag, [flag])
                return

            try:
                raw = self._read_body()
            except BodyTooLarge:
                self._refuse(request_id, session, 413, "request_too_large", "the request body is too large", "body_too_large", ["body_too_large"])
                return
            except (BadBody, ValueError) as exc:
                self._refuse(request_id, session, 400, "invalid_request_error", str(exc), "bad_body", ["bad_body"])
                return
            text = raw.decode("utf-8", errors="replace")

            flags: list[str] = []
            user_agent = self.headers.get("User-Agent", "")
            if session.user_agent_prefix and not user_agent.startswith(session.user_agent_prefix):
                flags.append("fingerprint_mismatch")

            try:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("the body is not a JSON object")
            except ValueError as exc:
                self._refuse(request_id, session, 400, "invalid_request_error", f"the body is not valid JSON: {exc}", "bad_json", [*flags, "bad_json"], text)
                return

            model = parsed.get("model")
            if not isinstance(model, str) or model not in session.models:
                self._refuse(
                    request_id, session, 400, "invalid_request_error",
                    f"model {model!r} is not allowed in this session", "model_rejected", [*flags, "model_rejected"], text,
                    requested_model=model if isinstance(model, str) else None,
                )
                return
            blocked = server_tools_in(parsed)
            if blocked:
                self._refuse(
                    request_id, session, 400, "invalid_request_error",
                    "server-side tools are not allowed in this session: " + ", ".join(blocked),
                    "server_tool_rejected", [*flags, "server_tool_rejected"], text, server_tools=blocked,
                )
                return
            counted = path == MESSAGES
            if counted:
                exhausted = gateway.budget_problem(session)
                if exhausted:
                    self._refuse(request_id, session, 403, "permission_error", exhausted, "budget_exhausted", [*flags, "budget_exhausted"], text)
                    return

            streamed = bool(parsed.get("stream"))
            gateway.log(
                session.id,
                {
                    "event": "request",
                    "id": request_id,
                    "session": session.id,
                    "client": self.client,
                    "method": self.command,
                    "path": path,
                    "query": query,
                    "user_agent": user_agent,
                    "headers": {n: self.headers.get(n) for n in LOGGED_REQUEST_HEADERS if self.headers.get(n)},
                    "model": model,
                    "stream": streamed,
                    "tool_types": tool_types(parsed),
                    "flags": flags,
                    "body": text,
                },
            )
            self._forward(request_id, session, path, query, raw, streamed, counted, flags)

        # -- forward and relay

        def _forward(self, request_id, session, path, query, raw, streamed, counted, flags) -> None:
            started = gateway.clock()
            url = gateway.base_path + path + (f"?{query}" if query else "")
            captured = bytearray()
            status = 0
            response_headers: dict[str, str | None] = {}
            disconnected = False
            upstream_error = None
            connection = None
            try:
                connection = gateway.connect()
                connection.request(self.command, url, body=raw, headers=gateway.upstream_headers(self.headers))
                upstream = connection.getresponse()
                status = upstream.status
                response_headers = {n: upstream.getheader(n) for n in LOGGED_RESPONSE_HEADERS}
                length = upstream.getheader("Content-Length")
                self.send_response_only(status, upstream.reason)
                for name, value in upstream.getheaders():
                    if name.lower() not in HOP_BY_HOP and name.lower() != "content-length":
                        self.send_header(name, value)
                self.send_header("Connection", "close")
                chunked = length is None
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", length)
                self.end_headers()
                self.wfile.flush()
                while True:
                    try:
                        block = upstream.read1(65536)
                    except (OSError, http.client.HTTPException) as exc:
                        # The model API broke off. The chunked body is left unfinished, so the agent sees an error.
                        upstream_error = f"{type(exc).__name__}: {exc}"
                        break
                    if not block:
                        if chunked:
                            self._write(b"0\r\n\r\n")
                        break
                    if len(captured) < gateway.max_body_bytes:
                        captured += block
                    if not self._write(f"{len(block):x}\r\n".encode("ascii") + block + b"\r\n" if chunked else block):
                        disconnected = True  # the agent went away. What was seen is still counted
                        break
            except (OSError, http.client.HTTPException, ValueError) as exc:
                upstream_error = f"{type(exc).__name__}: {exc}"
                if not status:
                    self._error(502, "api_error", "the gateway could not reach the model API")
            finally:
                if connection is not None:
                    connection.close()

            parsed = parse_response(bytes(captured), streamed and 200 <= status < 300)
            usage = parsed["usage"]
            response_flags = list(flags)
            returned = parsed["model"]
            if returned and not any(returned == m or returned.startswith(m + "-") for m in session.models):
                response_flags.append("response_model_differs")  # a fallback or another model answered
            # Every call that reached the model API counts as a request, so failing calls cannot be hammered for free.
            counts = counted and status != 0
            if counts:
                gateway.add_usage(session.id, usage)
            record = {
                "event": "response",
                "id": request_id,
                "session": session.id,
                "status": status or 502,
                "duration_ms": int((gateway.clock() - started) * 1000),
                "headers": response_headers,
                "usage": usage,
                "usage_input_total": input_total(usage),
                "counted": counts,
                "response_model": returned,
                "stop_reason": parsed["stop_reason"],
                "flags": response_flags,
                "client_disconnected": disconnected,
                "body": bytes(captured).decode("utf-8", errors="replace"),
            }
            if upstream_error:
                record["upstream_error"] = upstream_error
            gateway.log(session.id, record)

    return Handler


def make_server(gateway: Gateway, host: str, port: int) -> ThreadingHTTPServer:
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 128

    server = Server((host, port), make_handler(gateway))
    server.gateway = gateway
    return server


# --- command line ---------------------------------------------------------------------


def _cmd_serve(args: argparse.Namespace) -> int:
    key = os.environ.get(args.key_env)
    if not key:
        print(f"Set the real API key in the environment variable {args.key_env} first.", file=sys.stderr)
        return 1
    gateway = Gateway(args.upstream, key, args.sessions, args.log_dir)
    server = make_server(gateway, args.host, args.port)
    print(f"Gateway on {args.host}:{server.server_address[1]}, upstream {args.upstream}, logs in {args.log_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_session(args: argparse.Namespace) -> int:
    try:
        if args.action == "add":
            if not args.bind and not args.allow_any_address:
                print("Give --bind with the sandbox's address, or --allow-any-address for development only.", file=sys.stderr)
                return 1
            token = add_session(
                args.sessions,
                session_id=args.id,
                models=args.model,
                max_input_tokens=args.max_input,
                max_output_tokens=args.max_output,
                max_requests=args.max_requests,
                bind=args.bind or [],
                minutes=args.minutes,
                user_agent_prefix=args.user_agent_prefix,
            )
            print("Token for the agent's API key (shown once, and only its hash is stored):")
            print(token)
        elif args.action == "revoke":
            revoke_session(args.sessions, args.id)
            print(f"Revoked {args.id}")
        elif args.action == "usage":
            session = load_sessions(args.sessions).get(args.id)
            if session is None:
                print(f"No session {args.id}", file=sys.stderr)
                return 1
            used = Gateway("http://localhost", "unused", args.sessions, args.log_dir).usage(args.id)
            print(
                f"{args.id}: {used.requests} requests, {used.input_tokens} input tokens (of {session.max_input_tokens}), "
                f"{used.output_tokens} output tokens (of {session.max_output_tokens})"
                + (", revoked" if session.revoked else "")
            )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gateway", description="A thin model gateway (spike).")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the gateway")
    serve.add_argument("--upstream", default="https://api.anthropic.com")
    serve.add_argument("--key-env", default="ANTHROPIC_API_KEY", help="environment variable holding the real key")
    serve.add_argument("--sessions", type=Path, required=True)
    serve.add_argument("--log-dir", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(run=_cmd_serve)

    session = commands.add_parser("session", help="manage sessions")
    actions = session.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="add a session and print its token")
    add.add_argument("--sessions", type=Path, required=True)
    add.add_argument("--id", required=True)
    add.add_argument("--model", action="append", required=True, help="a pinned model id. Repeat for more")
    add.add_argument("--max-input", type=int, default=20_000_000)
    add.add_argument("--max-output", type=int, default=600_000)
    add.add_argument("--max-requests", type=int, default=800)
    add.add_argument("--bind", action="append", help="the sandbox's address. Repeat for more")
    add.add_argument("--allow-any-address", action="store_true", help="development only")
    add.add_argument("--minutes", type=float, help="the token expires after this long")
    add.add_argument("--user-agent-prefix", help="the agent's fingerprint. A request that differs is flagged")
    revoke = actions.add_parser("revoke", help="end a session now")
    revoke.add_argument("--sessions", type=Path, required=True)
    revoke.add_argument("--id", required=True)
    usage = actions.add_parser("usage", help="show what a session has used")
    usage.add_argument("--sessions", type=Path, required=True)
    usage.add_argument("--log-dir", type=Path, required=True)
    usage.add_argument("--id", required=True)
    session.set_defaults(run=_cmd_session)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
