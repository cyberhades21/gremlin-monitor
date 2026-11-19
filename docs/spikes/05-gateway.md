# Spike 5, the model gateway

Status: built and tested against a stand-in for the model API, 2026-11-19. Later the same day it was run on real Docker networks, in front of a local Ollama model, with OpenCode 2.3.2 as the agent (`01-opencode-hands-on.md`). Not yet run against the real Anthropic API. Spike code (`spikes/gateway/`), which spec 11 says is never promoted without a rewrite.

## What it is

A small server between a candidate's agent and the model API. The agent gets a session token as its API key and the gateway as its base URL. The real key lives only in the gateway's environment, so no sandbox ever holds it. It never changes a request body or a response.

It was written early because every agent option in the desk check (`01-agent-desk-check.md`) ends up calling the same API, so the gateway does not depend on which agent wins. It also covers two items the manual pilot would otherwise do by hand: a network rule that lets a session reach the model API and nothing else, and a spend limit per session.

## Against the exit criteria in spec 11

| Criterion | State |
|---|---|
| Token swap | Shown. The model API receives the real key, never the token, and no `authorization`, `cookie` or forwarding headers. A bearer token works as well as `x-api-key` |
| Cap | Shown. Input tokens (including cache reads and writes), output tokens and request count, per session. The totals are rebuilt from the log on restart |
| Pin by rejection | Shown. Any model not pinned for the session is refused with a 400, and so are server-side tools that reach outside (web search, web fetch, code execution, the MCP connector). The agent's own tools (bash, text editor, custom tools) pass |
| Full logging | Shown. Every request and response is written whole to `<log-dir>/<session>.jsonl`: bodies, usage, status, a few headers, timing. Streams are logged exactly as relayed. The key and the token never appear |
| Binding to the sandbox address | Shown by address check against the connection's source address, with IPv4-in-IPv6 addresses normalised. Shown on real Docker networks: the right token used from a second sandbox is refused with a 403 (`spikes/sandbox/rig.py wiring`, check P5) |
| Fingerprint flag | Shown for a user-agent prefix: a mismatch is flagged in the log, not refused. OpenCode 2.3.2 sends `opencode/latest/2.3.2/cli`. The flag has not been run with that prefix set |
| Refusal or fallback model visible per call | Shown. Each response record carries the model that answered, the stop reason, and a `response_model_differs` flag when it is not the pinned model or a dated form of it |
| Token expiry on submit | Shown. `session revoke` takes effect on the next request, and a session can also expire after a set time |
| Run the leading agent through it | Done with a local model: OpenCode 2.3.2, through the gateway, to Ollama. Streaming, tool calls and the path all work (see "What a real agent showed" below). **Not done against the real API**, which needs a key |

## What it handles because of Anthropic's gateway protocol

Taken from the Agent SDK and Claude Code docs (see the desk check). Streaming responses are relayed as they arrive, not buffered, because a buffering gateway makes the agent stall. Request bodies are forwarded byte for byte. `anthropic-version` and `anthropic-beta` are passed on unchanged. Error bodies and `retry-after` pass through. `POST /v1/messages` (with `?beta=true`), `POST /v1/messages/count_tokens` and the agent's `HEAD /api/hello` probe are served, and every other path gets a 404 in the provider's error format, so it cannot be used as a general proxy.

## Running it

```
uv run python spikes/gateway/gateway.py session add --sessions pilot/gateway/sessions.json --id S-001 --model claude-opus-5-5 --bind <sandbox address> --minutes 90
uv run python spikes/gateway/gateway.py serve --sessions pilot/gateway/sessions.json --log-dir pilot/gateway/logs --host 0.0.0.0 --port 8080
uv run python spikes/gateway/gateway.py session usage --sessions pilot/gateway/sessions.json --log-dir pilot/gateway/logs --id S-001
uv run python spikes/gateway/gateway.py session revoke --sessions pilot/gateway/sessions.json --id S-001
```

`serve` reads the real key from `ANTHROPIC_API_KEY` in its own environment (`--key-env` changes the name). `session add` prints the token once and stores only its hash. The running gateway notices changes to the sessions file, so sessions can be added and revoked without a restart. `session add` refuses to make a session with no address unless `--allow-any-address` is given, which is for development only.

The agent in the sandbox gets `ANTHROPIC_BASE_URL` set to the gateway and the token as its API key. The path has to come out as `/v1/messages`. For the Anthropic SDKs and Claude Code the base URL is the host alone. An agent built on a general AI SDK may want `/v1` on the end. The gateway log shows which, because a wrong path is logged as a 404.

## Where the sandbox network fits

For the manual pilot: the gateway runs on the VM, either as a process or in a container. Session containers join a Docker network created with `--internal`, which has no route out, and the gateway joins that network as well as one that does. The sandbox reaches the gateway by name and nothing else. Anthropic's own secure-deployment guide describes a tighter variant: `--network none` plus a Unix socket to the proxy. That would need the gateway to listen on a socket and a small forwarder inside the sandbox. The first variant is built and checked: `spikes/sandbox/rig.py wiring` makes the two networks and runs 18 checks against a local model. The sandbox has no route to the internet by name or by address, cannot reach the model server on the host directly, reaches the gateway by name, and the gateway rules in the table above all hold. All 18 pass. The tighter `--network none` variant is not built.

## Limits to know about

- A threaded standard-library server with no TLS on the listening side. It is for an internal network only. One process, no concurrency cap per session.
- Budgets are token counts, not money. Pricing differs by model, and with one pinned model the token caps are an adequate bound for a pilot.
- Logs hold every prompt and response, so they are personal data. They live outside git, with the other pilot data, and follow the retention rules in spec 12.
- Sessions are managed through a file and a command. There is no admin interface.
- The sandbox address binding assumes the gateway sees the container's own address as the source, which is the case on a Docker bridge network. Check it on the VM.
- Each response closes its connection. Clients cope with that, and it removes a class of framing mistakes.
- Server-side tools are blocked by type prefix (`web_search`, `web_fetch`, `code_execution`). A new provider-side tool with another name would pass until the list is extended. The model-API-side `mcp_servers` parameter is blocked outright.

## What a real agent showed (OpenCode 2.3.2, local model, 2026-11-19)

1. Streaming works and the path matches. OpenCode calls `POST /v1/messages?beta=true`, and its base URL has to end in `/v1` (it is built on a general AI SDK, as this note guessed). The streamed reply arrived in 115 pieces over 2.6 seconds, so the gateway does not buffer.
2. Besides the main call OpenCode makes one more call per session, to title it, and a compaction call whenever its context fills. Both ask for the same model as the main call, so the pin rejects nothing. Its three hidden agents (title, summary, compaction) have no model of their own. The summary agent was never seen.
3. A made-up token and a missing token are refused with a 401 and nothing reaches the model.
4. The right token used from another sandbox is refused with a 403.
5. Not tested with a real key: usage fields from the real API, cache read and write counts, and the real model's refusal behaviour.

## What a Gate 1b version needs that this does not have

An asynchronous server that scales past a thread per connection, listening on a Unix socket or with TLS, events written through the `EventSink` interface instead of a file, the sandbox provider telling it each session's address, per-model pricing, an admin interface with its own authentication, and a per-session concurrency limit.
