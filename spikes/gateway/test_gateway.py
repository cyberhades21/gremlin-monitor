"""Tests for the gateway spike, against a stand-in for the model API."""

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import gateway as gw

MODEL = "claude-opus-5-5"
REAL_KEY = "sk-ant-real-key-for-the-tests"
UA = "claude-cli/9.9 (test)"


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class FakeUpstream:
    """Stands in for api.anthropic.com. Records what it is sent."""

    def __init__(self):
        self.requests: list[dict] = []
        self.release = threading.Event()  # a stream waits for this between its first event and the rest
        self.release.set()
        self.status = 200
        self.response_model: str | None = None
        self.extra_headers: dict[str, str] = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, data, status=200):
                payload = json.dumps(data).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("request-id", "req_fake")
                for name, value in owner.extra_headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                owner.requests.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                )
                try:
                    request = json.loads(body or b"{}")
                except ValueError:
                    request = {}
                if owner.status != 200:
                    self._json({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}, owner.status)
                    return
                model = owner.response_model or request.get("model", MODEL)
                if self.path.partition("?")[0] == "/v1/messages/count_tokens":
                    self._json({"input_tokens": 123})
                elif request.get("stream"):
                    self._stream(model)
                else:
                    self._json(
                        {
                            "id": "msg_1",
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [{"type": "text", "text": "hi"}],
                            "stop_reason": "end_turn",
                            "usage": {
                                "input_tokens": 100,
                                "cache_creation_input_tokens": 10,
                                "cache_read_input_tokens": 50,
                                "output_tokens": 40,
                            },
                        }
                    )

            def _stream(self, model):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def chunk(text):
                    data = text.encode("utf-8")
                    self.wfile.write(f"{len(data):x}\r\n".encode("ascii") + data + b"\r\n")
                    self.wfile.flush()

                usage = {"input_tokens": 100, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 50, "output_tokens": 1}
                chunk(sse("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": model, "usage": usage}}))
                owner.release.wait(timeout=10)
                chunk(
                    sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}})
                    + sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 40}})
                    + sse("message_stop", {"type": "message_stop"})
                )
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


class Rig:
    """A gateway in front of the fake upstream, with one session, S-001."""

    def __init__(self, tmp_path, *, upstream_url=None, max_body_bytes=gw.MAX_BODY_BYTES):
        self.now = 1_000_000.0
        self.upstream = FakeUpstream()
        self.sessions = tmp_path / "sessions.json"
        self.logs = tmp_path / "logs"
        self.ids: dict[str, str] = {}  # token -> session id
        self.token = self.add("S-001")
        self.gateway = gw.Gateway(
            upstream_url or self.upstream.url,
            REAL_KEY,
            self.sessions,
            self.logs,
            clock=lambda: self.now,
            max_body_bytes=max_body_bytes,
            upstream_timeout=10,
        )
        self.server = gw.make_server(self.gateway, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def add(self, session_id, **options):
        defaults = dict(
            models=[MODEL], max_input_tokens=10_000, max_output_tokens=10_000, max_requests=100,
            bind=["127.0.0.1"], clock=lambda: self.now,
        )
        token = gw.add_session(self.sessions, session_id=session_id, **{**defaults, **options})
        self.ids[token] = session_id
        return token

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def call(self, method, path, body=None, *, token=None, headers=None, raw=None):
        """One request. `token=False` sends none. Returns (status, lowercase headers, body bytes).

        The gateway finishes its accounting after it has sent the last byte, so for a messages call this
        waits for the gateway's own record before returning. Otherwise a test that looks at the usage, or
        sends the next request, straight away would race the gateway.
        """
        sent = {"content-type": "application/json", "anthropic-version": "2023-06-01", "user-agent": UA}
        if token is not False:
            sent["x-api-key"] = token or self.token
        sent.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        session_id = self.ids.get(token or self.token) if token is not False else None
        watch = session_id if method == "POST" and path.partition("?")[0] in (gw.MESSAGES, gw.COUNT_TOKENS) else None
        before = len(self._finished(watch)) if watch else 0
        connection = self.connection()
        try:
            connection.request(method, path, body=data, headers=sent)
            response = connection.getresponse()
            result = response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        finally:
            connection.close()
        if watch:
            self._finished(watch, wait=before + 1)
        return result

    def _read(self, session_id):
        path = self.logs / f"{session_id}.jsonl"
        return [json.loads(line) for line in path.read_bytes().decode("utf-8").splitlines()] if path.exists() else []

    def _finished(self, session_id, *, wait=0):
        """The records that close a request: its response, or its refusal."""
        deadline = time.time() + 5
        while True:
            found = [r for r in self._read(session_id) if r["event"] in ("response", "rejected")]
            if len(found) >= wait or time.time() > deadline:
                return found
            time.sleep(0.01)

    def records(self, session_id="S-001", event=None, *, wait=0):
        """The log's records. With `wait`, polls until that many of `event` exist, since logging follows the reply."""
        deadline = time.time() + 5
        while True:
            found = [r for r in self._read(session_id) if event is None or r["event"] == event]
            if len(found) >= wait or time.time() > deadline:
                return found
            time.sleep(0.01)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.upstream.stop()


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.close()


def message(model=MODEL, **extra):
    return {"model": model, "max_tokens": 100, "messages": [{"role": "user", "content": "hello"}], **extra}


# --- what is passed on -------------------------------------------------------------


def test_a_request_is_passed_on_with_the_real_key_and_nothing_else_changed(rig):
    body = json.dumps(message(system="be brief")).encode("utf-8")

    status, headers, answer = rig.call(
        "POST", "/v1/messages?beta=true", raw=body, headers={"anthropic-beta": "tools-2024-04-04", "cookie": "a=b"}
    )

    assert status == 200 and json.loads(answer)["usage"]["output_tokens"] == 40
    seen = rig.upstream.requests[0]
    assert seen["body"] == body  # the body is byte for byte what was sent
    assert seen["path"] == "/v1/messages?beta=true"
    assert seen["headers"]["x-api-key"] == REAL_KEY
    assert rig.token not in "".join(seen["headers"].values())
    assert "authorization" not in seen["headers"] and "cookie" not in seen["headers"]
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["anthropic-beta"] == "tools-2024-04-04"
    assert seen["headers"]["accept-encoding"] == "identity"
    assert headers["request-id"] == "req_fake"
    assert rig.gateway.usage("S-001") == gw.Usage(input_tokens=160, output_tokens=40, requests=1)


def test_a_bearer_token_is_accepted_too(rig):
    status, _, _ = rig.call("POST", "/v1/messages", message(), token=False, headers={"authorization": f"Bearer {rig.token}"})

    assert status == 200
    sent = rig.upstream.requests[0]["headers"]
    assert sent["x-api-key"] == REAL_KEY and "authorization" not in sent


def test_errors_from_the_model_api_pass_through_unchanged(rig):
    rig.upstream.status = 529
    rig.upstream.extra_headers = {"retry-after": "7"}

    status, headers, answer = rig.call("POST", "/v1/messages", message())

    assert status == 529 and headers["retry-after"] == "7"
    assert json.loads(answer)["error"]["type"] == "overloaded_error"
    assert rig.records(event="response", wait=1)[0]["status"] == 529
    assert rig.gateway.usage("S-001") == gw.Usage(0, 0, 1)  # it still counts as a request


def test_an_unreachable_model_api_gives_a_502_in_the_providers_format(tmp_path):
    rig = Rig(tmp_path, upstream_url="http://127.0.0.1:1")
    try:
        status, _, answer = rig.call("POST", "/v1/messages", message())

        assert status == 502 and json.loads(answer)["error"]["type"] == "api_error"
        record = rig.records(event="response", wait=1)[0]
        assert "upstream_error" in record and record["counted"] is False
        assert rig.gateway.usage("S-001").requests == 0  # the model API never saw it
    finally:
        rig.close()


def test_a_chunked_request_body_is_read(rig):
    body = json.dumps(message()).encode("utf-8")
    connection = rig.connection()
    connection.request(
        "POST", "/v1/messages", body=iter([body[:10], body[10:]]), encode_chunked=True,
        headers={"x-api-key": rig.token, "content-type": "application/json"},
    )
    response = connection.getresponse()
    response.read()
    connection.close()

    assert response.status == 200 and rig.upstream.requests[0]["body"] == body


# --- what is refused --------------------------------------------------------------


def test_bad_tokens_are_refused_and_nothing_reaches_the_model_api(rig):
    assert rig.call("POST", "/v1/messages", message(), token="gw-not-a-real-token")[0] == 401
    assert rig.call("POST", "/v1/messages", message(), token=False)[0] == 401

    rig.now += 100
    expiring = rig.add("S-002", minutes=1)
    assert rig.call("POST", "/v1/messages", message(), token=expiring)[0] == 200
    rig.now += 61
    status, _, answer = rig.call("POST", "/v1/messages", message(), token=expiring)
    assert status == 401 and json.loads(answer)["error"]["type"] == "authentication_error"

    gw.revoke_session(rig.sessions, "S-001")
    assert rig.call("POST", "/v1/messages", message())[0] == 401

    assert [r["flags"] for r in rig.records("S-001", "rejected")] == [["revoked"]]
    assert [r["flags"] for r in rig.records("S-002", "rejected")] == [["expired"]]
    assert [r["reason"] for r in rig.records("_unauthenticated")] == ["unknown_token", "unknown_token"]
    assert len(rig.upstream.requests) == 1  # only the one call that was allowed


def test_only_the_messages_endpoints_are_served(rig):
    for method, path in [
        ("GET", "/v1/models"),
        ("POST", "/v1/complete"),
        ("POST", "/v1/files"),
        ("POST", "/v1/messages/batches"),
        ("GET", "/v1/messages"),
        ("POST", "/v1/messages/"),
        ("GET", "/"),
        ("GET", "http://example.com/v1/messages"),
    ]:
        status, _, answer = rig.call(method, path, message() if method == "POST" else None)
        assert status == 404, (method, path)
        assert json.loads(answer)["error"]["type"] == "not_found_error"

    assert rig.call("HEAD", "/api/hello", token=False)[0] == 200  # the agent's connection probe needs no key
    assert rig.upstream.requests == []


def test_only_the_pinned_model_is_allowed(rig):
    status, _, answer = rig.call("POST", "/v1/messages", message(model="claude-sonnet-5-5"))
    assert status == 400 and json.loads(answer)["error"]["type"] == "invalid_request_error"
    assert "claude-sonnet-5-5" in json.loads(answer)["error"]["message"]

    assert rig.call("POST", "/v1/messages", {"max_tokens": 1, "messages": []})[0] == 400  # no model at all
    assert rig.call("POST", "/v1/messages/count_tokens", message(model="claude-haiku-5-5"))[0] == 400
    assert rig.call("POST", "/v1/messages/count_tokens", message())[0] == 200
    assert rig.call("POST", "/v1/messages", message())[0] == 200

    rejected = rig.records(event="rejected")
    assert [r["flags"] for r in rejected] == [["model_rejected"]] * 3
    assert rejected[0]["requested_model"] == "claude-sonnet-5-5" and "claude-sonnet-5-5" in rejected[0]["body"]
    assert len(rig.upstream.requests) == 2


def test_server_side_tools_are_refused_and_the_agents_own_tools_are_not(rig):
    for blocked in (
        message(tools=[{"type": "web_search_20250305", "name": "web_search"}]),
        message(tools=[{"type": "web_fetch_20250910", "name": "web_fetch"}]),
        message(tools=[{"type": "code_execution_20250522", "name": "code_execution"}]),
        message(mcp_servers=[{"type": "url", "url": "https://example.com/mcp", "name": "x"}]),
    ):
        status, _, answer = rig.call("POST", "/v1/messages", blocked)
        assert status == 400 and "server-side tools are not allowed" in json.loads(answer)["error"]["message"]

    allowed = [
        message(tools=[{"name": "web_search", "description": "my own", "input_schema": {"type": "object"}}]),  # a custom tool
        message(tools=[{"type": "bash_20250124", "name": "bash"}]),  # runs in the sandbox
        message(tools=5),  # not valid, but it must not crash the gateway. The model API judges it
    ]
    assert [rig.call("POST", "/v1/messages", body)[0] for body in allowed] == [200, 200, 200]
    assert [r["server_tools"] for r in rig.records(event="rejected")] == [
        ["web_search_20250305"], ["web_fetch_20250910"], ["code_execution_20250522"], ["mcp_servers"],
    ]


def test_a_body_that_is_not_json_or_is_too_large_is_refused(tmp_path):
    rig = Rig(tmp_path, max_body_bytes=2000)
    try:
        assert rig.call("POST", "/v1/messages", raw=b"{not json")[0] == 400
        assert rig.call("POST", "/v1/messages", raw=b'["a list"]')[0] == 400
        status, _, answer = rig.call("POST", "/v1/messages", raw=json.dumps(message(system="x" * 5000)).encode("utf-8"))
        assert status == 413 and json.loads(answer)["error"]["type"] == "request_too_large"
        assert rig.upstream.requests == []
    finally:
        rig.close()


# --- the budget, the address and the fingerprint ---------------------------------


def test_the_budget_ends_the_session_and_survives_a_restart(rig):
    capped = rig.add("S-CAP", max_output_tokens=100)

    assert [rig.call("POST", "/v1/messages", message(), token=capped)[0] for _ in range(3)] == [200, 200, 200]
    status, _, answer = rig.call("POST", "/v1/messages", message(), token=capped)
    assert status == 403 and json.loads(answer)["error"]["type"] == "permission_error"
    assert "output token budget" in json.loads(answer)["error"]["message"]
    assert rig.call("POST", "/v1/messages/count_tokens", message(), token=capped)[0] == 200  # counting costs nothing

    restarted = gw.Gateway(rig.upstream.url, REAL_KEY, rig.sessions, rig.logs)
    assert restarted.usage("S-CAP") == gw.Usage(input_tokens=480, output_tokens=120, requests=3)
    assert len(rig.upstream.requests) == 4  # three messages and the count


def test_the_request_and_input_budgets_also_end_a_session(rig):
    two = rig.add("S-TWO", max_requests=2)
    results = [rig.call("POST", "/v1/messages", message(), token=two) for _ in range(3)]
    assert [r[0] for r in results] == [200, 200, 403]
    assert "request budget" in json.loads(results[2][2])["error"]["message"]

    small = rig.add("S-SMALL", max_input_tokens=200)  # each call uses 160 input tokens, so the second is the last
    results = [rig.call("POST", "/v1/messages", message(), token=small) for _ in range(3)]
    assert [r[0] for r in results] == [200, 200, 403]
    assert "input token budget" in json.loads(results[2][2])["error"]["message"]
    assert rig.records("S-SMALL", "rejected")[0]["flags"] == ["budget_exhausted"]


def test_parallel_requests_are_all_counted_and_every_log_line_is_whole(rig):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: rig.call("POST", "/v1/messages", message())[0], range(24)))

    assert statuses == [200] * 24
    assert len(rig.records(event="response", wait=24)) == 24
    assert rig.gateway.usage("S-001") == gw.Usage(input_tokens=24 * 160, output_tokens=24 * 40, requests=24)
    assert sorted(r["id"] for r in rig.records(event="request")) == sorted(r["id"] for r in rig.records(event="response"))


def test_a_token_is_bound_to_the_sandboxs_address(rig):
    elsewhere = rig.add("S-ELSEWHERE", bind=["10.9.9.9"])

    status, _, answer = rig.call("POST", "/v1/messages", message(), token=elsewhere)

    assert status == 403 and "cannot be used from this address" in json.loads(answer)["error"]["message"]
    assert rig.records("S-ELSEWHERE", "rejected")[0]["flags"] == ["address_rejected"]
    assert rig.records("S-ELSEWHERE", "rejected")[0]["client"] == "127.0.0.1"
    assert rig.upstream.requests == []
    assert gw.normalise_address("::ffff:172.20.0.5") == "172.20.0.5" and gw.normalise_address("172.20.0.5") == "172.20.0.5"


def test_a_request_that_does_not_look_like_the_agent_is_flagged_but_served(rig):
    marked = rig.add("S-PRINT", user_agent_prefix="claude-cli/")

    assert rig.call("POST", "/v1/messages", message(), token=marked, headers={"user-agent": "curl/8.5"})[0] == 200
    assert rig.call("POST", "/v1/messages", message(), token=marked, headers={"user-agent": "claude-cli/1.2"})[0] == 200

    requests = rig.records("S-PRINT", "request")
    responses = rig.records("S-PRINT", "response", wait=2)
    assert [r["flags"] for r in requests] == [["fingerprint_mismatch"], []]
    assert [r["flags"] for r in responses] == [["fingerprint_mismatch"], []]


def test_a_fallback_model_is_visible_in_the_log(rig):
    rig.upstream.response_model = "claude-sonnet-5-5"
    rig.call("POST", "/v1/messages", message())
    rig.upstream.response_model = "claude-opus-5-5-20261101"  # a dated form of the pinned model is not a fallback
    rig.call("POST", "/v1/messages", message())

    responses = rig.records(event="response", wait=2)
    assert [r["flags"] for r in responses] == [["response_model_differs"], []]
    assert [r["response_model"] for r in responses] == ["claude-sonnet-5-5", "claude-opus-5-5-20261101"]


# --- streaming ------------------------------------------------------------------------


def test_a_stream_is_relayed_as_it_arrives_and_counted(rig):
    rig.upstream.release.clear()  # the model API holds back everything after the first event
    connection = rig.connection()
    connection.request(
        "POST", "/v1/messages", body=json.dumps(message(stream=True)).encode("utf-8"),
        headers={"x-api-key": rig.token, "content-type": "application/json"},
    )
    response = connection.getresponse()  # a buffering gateway would never get here, because the end is held back
    assert response.status == 200 and response.getheader("Transfer-Encoding") == "chunked"
    first = response.read1(65536)
    assert b"message_start" in first and b"message_stop" not in first

    rig.upstream.release.set()
    rest = response.read()
    connection.close()

    assert b"message_delta" in rest and rest.rstrip().endswith(b'"type": "message_stop"}')
    record = rig.records(event="response", wait=1)[0]
    assert record["usage"] == {
        "input_tokens": 100, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 50, "output_tokens": 40,
    }
    assert (record["response_model"], record["stop_reason"]) == (MODEL, "end_turn")
    assert record["body"] == (first + rest).decode("utf-8")  # the log holds the stream exactly as relayed
    assert rig.gateway.usage("S-001") == gw.Usage(160, 40, 1)


def test_an_agent_that_leaves_mid_stream_does_not_break_the_gateway(rig):
    rig.upstream.release.clear()
    connection = rig.connection()
    connection.request(
        "POST", "/v1/messages", body=json.dumps(message(stream=True)).encode("utf-8"),
        headers={"x-api-key": rig.token, "content-type": "application/json"},
    )
    connection.getresponse().read1(65536)
    connection.close()  # gone before the rest arrives
    rig.upstream.release.set()

    record = rig.records(event="response", wait=1)[0]
    assert record["counted"] is True and record["usage"]["input_tokens"] == 100
    assert rig.call("POST", "/v1/messages", message())[0] == 200  # and the next request is served


# --- the log -------------------------------------------------------------------------


def test_every_request_and_response_is_logged_in_full_without_any_secret(rig):
    sent = message(system="be brief", tools=[{"name": "run", "description": "d", "input_schema": {"type": "object"}}])
    rig.call("POST", "/v1/messages?beta=true", sent, headers={"anthropic-beta": "x-beta"})

    request = rig.records(event="request")[0]
    response = rig.records(event="response", wait=1)[0]
    assert json.loads(request["body"]) == sent
    assert (request["session"], request["client"], request["method"], request["path"], request["query"]) == (
        "S-001", "127.0.0.1", "POST", "/v1/messages", "beta=true",
    )
    assert request["headers"]["anthropic-beta"] == "x-beta" and request["tool_types"] == ["custom"]
    assert request["user_agent"] == UA and request["model"] == MODEL and request["stream"] is False
    assert json.loads(response["body"])["content"][0]["text"] == "hi"
    assert (response["status"], response["usage_input_total"], response["counted"]) == (200, 160, True)
    assert request["id"] == response["id"] and request["time"].startswith("1970-01-12T")

    everything = (rig.logs / "S-001.jsonl").read_bytes().decode("utf-8")
    assert REAL_KEY not in everything and rig.token not in everything


# --- sessions, tokens and the command line --------------------------------------------


def test_the_sessions_file_holds_only_a_hash_of_the_token(rig):
    stored = rig.sessions.read_bytes().decode("utf-8")

    assert rig.token not in stored and gw.hashlib.sha256(rig.token.encode("utf-8")).hexdigest() in stored


def test_session_ids_cannot_reach_outside_the_log_folder(tmp_path):
    for bad in ("../x", "a/b", "", ".hidden", "x" * 80):
        with pytest.raises(ValueError):
            gw.add_session(
                tmp_path / "s.json", session_id=bad, models=[MODEL], max_input_tokens=1, max_output_tokens=1,
                max_requests=1, bind=["127.0.0.1"],
            )


def test_the_command_line_adds_revokes_and_reports(tmp_path, capsys):
    sessions, logs = tmp_path / "s.json", tmp_path / "logs"

    assert gw.main(["session", "add", "--sessions", str(sessions), "--id", "S-9", "--model", MODEL]) == 1
    assert "--bind" in capsys.readouterr().err  # a session with no address is refused unless it is asked for

    assert gw.main(["session", "add", "--sessions", str(sessions), "--id", "S-9", "--model", MODEL, "--bind", "172.20.0.5"]) == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith("gw-") and token not in sessions.read_bytes().decode("utf-8")
    assert gw.main(["session", "add", "--sessions", str(sessions), "--id", "S-9", "--model", MODEL, "--bind", "1.1.1.1"]) == 1

    assert gw.main(["session", "usage", "--sessions", str(sessions), "--log-dir", str(logs), "--id", "S-9"]) == 0
    assert "S-9: 0 requests, 0 input tokens (of 20000000)" in capsys.readouterr().out
    assert gw.main(["session", "revoke", "--sessions", str(sessions), "--id", "S-9"]) == 0
    assert gw.load_sessions(sessions)["S-9"].revoked is True


def test_serving_needs_the_real_key_in_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GATEWAY_TEST_KEY", raising=False)

    assert gw.main(["serve", "--key-env", "GATEWAY_TEST_KEY", "--sessions", str(tmp_path / "s.json"), "--log-dir", str(tmp_path)]) == 1
    assert "GATEWAY_TEST_KEY" in capsys.readouterr().err


def test_usage_is_read_from_a_stream_that_carries_cumulative_counts():
    stream = (
        sse("message_start", {"type": "message_start", "message": {"model": MODEL, "usage": {"input_tokens": 5, "output_tokens": 1}}})
        + sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"input_tokens": 7, "output_tokens": 30}})
    ).encode("utf-8")

    parsed = gw.parse_response(stream, streamed=True)

    assert parsed["usage"]["input_tokens"] == 7 and parsed["usage"]["output_tokens"] == 30
    assert (parsed["model"], parsed["stop_reason"]) == (MODEL, "tool_use")
    assert gw.parse_response(b"not json", streamed=False)["usage"]["output_tokens"] == 0
