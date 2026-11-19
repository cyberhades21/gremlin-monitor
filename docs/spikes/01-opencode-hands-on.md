# Spike 1, hands-on: OpenCode through the gateway to a local model

Status: run 2026-11-19, on one laptop. A local model (Ollama) stood in for Claude and no Anthropic key was used. So this shows how the agent behaves, what it sends and how it can be locked down. It shows nothing about answer quality, cost, or how hard a problem is for a strong model. The agent choice stays with the engineer. The code is `spikes/sandbox/` (never promoted without a rewrite, spec 11).

## Result in brief

1. **OpenCode 2.3.2 passes all six checks in the hands-on box** from `01-agent-desk-check.md`, with the gateway in front and a sandbox that has no route out. Nothing here argues against making it the first choice. Two things still need a real key: how it behaves with real Claude, and what a session costs.
2. **The lock works.** A root-owned file at `/etc/opencode/opencode.json` loads last and wins. User config, project config, `OPENCODE_CONFIG` and `OPENCODE_CONFIG_CONTENT` all lose to it. A control run without the file shows the same attempts do work when it is absent, so the test means something.
3. **One gateway pin covers every call that was seen.** Each session makes a main request and a title request, and a compaction request whenever its context fills. All of them ask for the session's model. The agent's hidden agents have no model of their own.
4. **Five findings change what we would ship** (see "Findings that change the setup"). One of them, a deny rule that breaks an untouched agent, was my own mistake in a first draft of the config and was found by bisecting.
5. **The whole V7 and V8 procedure runs with this agent** (see "Running it for V7 and V8"). That part was a rehearsal of the mechanics. A 4-billion-parameter model's results say nothing about the problem.
6. **The Agent SDK harness also works behind the gateway**, as a plumbing check only. It asks for just the pinned model, and it brings baggage that OpenCode does not: attribution text naming Claude Code, a billing marker, a larger tool surface and 1.5 to 2 times the input per turn (see "The Agent SDK harness, a plumbing look").

## What was run

| Part | What |
|---|---|
| Machine | Windows laptop, Docker Desktop (engine 29.7.2), Ollama 0.19.0, a 6 GB laptop GPU |
| Model | `qwen3:4b-instruct` through Ollama's Anthropic-style `/v1/messages`, with a 16k context window (`gassess-qwen3-4b-16k`) |
| Agent | OpenCode v2.3.2. Release asset `opencode-linux-x64.tar.gz`, SHA-256 `563b5d663637226b6e3bb4bb6803281bc39b0d2cd88db06d674b6420aab009b0`, the digest GitHub publishes for that asset, checked by the Dockerfile at build time |
| Image | The toy package's environment image plus ripgrep, git and the agent (`spikes/sandbox/Dockerfile`). Run as the unprivileged user, read-only root file system, `--cap-drop ALL`, no new privileges, 2 GB and 2 CPUs |
| Network | Sandbox on a Docker `--internal` network. The gateway sits on that network and on one with a route out. Ollama is on the host |
| Gateway | `spikes/gateway/gateway.py`, unchanged |

`uv run python spikes/sandbox/rig.py wiring` checks the network and the gateway rules (18 of 18 pass, listed in `05-gateway.md`). `rig.py agent probe|override|matrix|bisect|prod-shape|task` are the agent experiments below. `rig.py runs <dir>` fills run folders for V7 and V8.

## The six checks

| # | Check from the desk check | Result |
|---|---|---|
| 1 | Install a pinned version from a checksum-verified file | **Pass.** Built from the release asset, hash checked in the Dockerfile. `opencode --version` prints `opencode v2.3.2` |
| 2 | A locked config in the managed location that a candidate cannot override | **Pass**, with one trap (see "Locking it") |
| 3 | Run it headless, and read how it ended | **Pass.** `opencode run --standalone --auto --format json` prints one JSON event per line and exits 0 when the agent stops. See "Running it for V7 and V8" |
| 4 | With only the gateway reachable, it starts without hanging, and the gateway sees only the pinned model | **Pass.** The CLI commands take 0.3 to 2.8 seconds offline and its own log shows no failed outbound calls. The gateway saw only `POST /v1/messages?beta=true` and only the session's model |
| 5 | A readable transcript | **Pass.** `opencode session export <id>` prints the whole session as JSON, with every tool call. The data folder holds a SQLite file and a log as well |
| 6 | The per-session key from the environment is what the gateway sees | **Pass.** `"apiKey": "{env:ANTHROPIC_API_KEY}"` is read from the environment, and the gateway attributed every request to the session that token belongs to |

## What OpenCode sends

From a run with the real model id `claude-opus-5-5` as a catalog model, answered by a temporary local alias of that name (`rig.py agent prod-shape`). So these are the request shapes OpenCode builds for Opus 5.5.

| | Main request | Title request |
|---|---|---|
| Path | `POST /v1/messages?beta=true` | the same |
| User agent | `opencode/latest/2.3.2/cli` (`latest` is the release channel, not a floating version) | the same |
| `anthropic-beta` | `interleaved-thinking-2025-05-14,thinking-binding-controls-2026-08-01` | the same |
| `thinking` | `{"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}` | the same |
| `max_tokens` | 128000 | 32000 |
| Tools | 10: edit, glob, grep, question, read, shell, skill, subagent, write, execute | none |
| System prompt | about 9,900 characters | about 1,900 characters |
| `cache_control` blocks | 4 | 2 |
| Input size, first request | about 4,600 tokens | |

- The run was repeated with the hardened managed file and gave the same shape. The system prompt was about 1,300 characters shorter (8,596) and the first request 4,296 tokens, with skills denied. The cause was not checked.
- The gateway must not allow-list beta values. This one is dated 2026-08-01 and will not be the last.
- With a custom model that is not in OpenCode's catalog (the local model), `thinking` is not sent and `max_tokens` is 4096.
- The `execute` and `question` tools are in the standard list. What `execute` does was not tested.

## Findings that change the setup

1. **Put `/v1` on the end of the base URL.** OpenCode builds on a general AI SDK, so `http://gw:8080/v1` produces `/v1/messages`. The desk check guessed this. Not tested without the `/v1`.
2. **The hidden calls use the session's model, not a small one.** The desk check listed this as unknown and a risk for the model pin. In 2.3.2 the title, summary and compaction agents have no model of their own (`opencode debug agents` shows `model: null`). The title call asked for the session model in every run. Compaction was forced by declaring a context window of 7,000 tokens (`rig.py agent compaction`). In 30 requests OpenCode made six compaction calls. Each carried no tools, the agent's full system prompt, a first message starting `<conversation-checkpoint>`, `max_tokens` 1,024, and the session model. So one pin at the gateway covers everything that was seen.
3. **Do not deny `integration.use`.** Bisecting a hardened config showed that denying it makes the provider unreachable, and an untouched agent fails with `Model unavailable`. Every other deny rule in the list below is safe.
4. **OpenCode's own cost figure leaves out the title call**, so it understates the real cost. The gateway's per-key usage is the source for cost, as the plan already says.
5. **An unreachable provider is retried for ever.** In the control run the agent printed `retry` events with growing delays up to 30 seconds and never gave up (it was killed by `timeout` after 240 seconds). A gateway outage will look like this to a candidate, and to a V7 run it will look like a very long run. The runbook's 10-minute outage rule covers it. A runner must tell a time limit from a retry loop by the last events, and the one in `rig.py` does.

Two more of the desk check's unknowns, answered. No `small_model` setting seems to be needed in version 2: the key is marked deprecated in the published schema and the title call did not need it. And switches for the catalog fetch and the update check exist (`OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_DISABLE_AUTOUPDATE` appear in the binary). Whether either call is made without them was not tested.

## Locking it

The managed file, as tested. `custom_model` is the local-model case. With the real model, drop the `models` entry and name `claude-opus-5-5`.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-opus-5-5",
  "update": "disable",
  "share": "disabled",
  "websearch": false,
  "enabled_providers": ["anthropic"],
  "providers": {"anthropic": {"settings": {"baseURL": "http://gw:8080/v1", "apiKey": "{env:ANTHROPIC_API_KEY}"}}},
  "permissions": [
    {"action": "webfetch", "resource": "*", "effect": "deny"},
    {"action": "websearch", "resource": "*", "effect": "deny"}
  ],
  "policies": [
    {"action": "provider.use", "resource": "*", "effect": "deny"},
    {"action": "provider.use", "resource": "anthropic", "effect": "allow"},
    {"action": "mcp.use", "resource": "*", "effect": "deny"},
    {"action": "plugin.use", "resource": "*", "effect": "deny"},
    {"action": "websearch.use", "resource": "*", "effect": "deny"},
    {"action": "integration.connect", "resource": "*", "effect": "deny"},
    {"action": "skill.use", "resource": "*", "effect": "deny"}
  ]
}
```

Set in the launcher's environment, which a candidate can unset, so they are comfort and not the lock: `OPENCODE_DISABLE_AUTOUPDATE=1`, `OPENCODE_DISABLE_MODELS_FETCH=1`, `OPENCODE_DISABLE_CLAUDE_CODE=1`, `OPENCODE_DISABLE_FILEWATCHER=1`.

What was shown:

- **Order.** `opencode debug config` lists the sources in the order they load. The managed file comes last, so its scalar keys (`model`, `update`, `share`) win.
- **Overrides lose.** Four candidate-owned sources each named a different model, and one also set `update: auto` and `share: auto`. The gateway saw only the pinned model (`rig.py agent override`).
- **The control proves it.** With no managed file, the same overrides took effect: the model name changed and the agent went looking for an unreachable host.
- **Widening loses.** A candidate file that re-allows web tools, MCP servers, plugins, skills and every provider left a hardened agent unchanged: no MCP server started, the same 10 tools, the same two requests (`rig.py agent matrix`, six variants). Without the managed file, the same widening started the MCP server and added a tool (`experimental_image_generate`).
- **Two layers, not one.** Even if a candidate got round the lock, the gateway rejects any other model and the network leaves no other route.

Not tested: `OPENCODE_DISABLE_PROJECT_CONFIG` (the managed file already beats a project file), plugins, and what a candidate can do from inside the terminal with the agent's own permissions. The agent's `shell` tool runs with the sandbox user's authority, as the desk check said.

## The interactive terminal UI

Candidates will use the terminal UI, not `opencode run`, so it was driven in a pseudo-terminal in the same sandbox (`rig.py agent tui`). It started with no route out, drew its screen (about 17 KB in the first 12 seconds), took a typed prompt, and showed the answer. The status line read `Build · gassess-qwen3-4b-16k · 7.7s · 62.5 tok/s` and `4.3K (26%)` of the context used. The gateway saw the same two requests as in headless mode, with the same user agent, `opencode/latest/2.3.2/cli`. So one fingerprint covers both modes.

The UI asked the terminal a few questions at startup (cursor position, terminal attributes, two colour queries). The test answered them, as a real terminal does. Whether it waits for ever when nothing answers was not tested. The test used `--standalone`.

## The Agent SDK harness, a plumbing look

The runner-up from the desk check, in a second image (`spikes/sandbox/Dockerfile.sdk`): `claude-agent-sdk` 0.2.185 from PyPI, released the same morning, which bundles the Claude Code binary in a 157 MB wheel. This tested how the harness behaves behind our gateway. It is not a decision to ship it: D15 stands, and the written answer from Anthropic is still needed.

Set up the way the desk check described: `ANTHROPIC_BASE_URL=http://gw:8080` (no `/v1`), the session token as `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` as the pin, the four disable switches, `setting_sources=[]`, `bypassPermissions`, and `WebSearch` and `WebFetch` disallowed. The context window was 32k, because its prompt is larger.

| | Result |
|---|---|
| Starts with no route out | Yes. A one-turn prompt took 7 to 26 seconds (the first included loading the model). The gateway saw no request it could not attribute to a session, so no `HEAD /api/hello` and no token counting |
| Requests | `POST /v1/messages?beta=true`, one per turn, streamed |
| Models requested | **Only the pinned one.** In a 15-request run on the V7 prompt, with no setting for its small model, every request named the pin. No title call, no background call |
| Tool calls | 13 tool calls went out and back through the gateway, with `stop_reason: "tool_use"` each time |
| Fingerprint | `claude-cli/2.1.328 (external, sdk-py, agent-sdk/0.2.185)` |
| Beta flags | Eight: `claude-code-20250219`, `interleaved-thinking-2025-05-14`, `context-management-2025-06-27`, `mid-conversation-tool-changes-2026-07-01`, `thinking-token-count-2026-05-13`, `advanced-tool-use-2025-11-20`, `effort-2025-11-24`, `thinking-display-updates-2026-08-18` |
| Other body fields | `thinking: {"type": "adaptive", "display": "updates"}`, `output_config: {"effort": "high"}`, a `context_management` edit, `max_tokens` 32000, and `metadata.user_id` holding a per-install device id and the session id |
| Size | The first request was about 9,200 input tokens and grew to 13,000 by turn 14. OpenCode's grew from 4,500 to 8,700 over a whole run. So each turn sends 1.5 to 2 times as much input |
| Transcript | `~/.claude/projects/-workspace/<session>.jsonl`, 120 KB for one turn because it holds the full prompt |
| Result object | A `ResultMessage` with duration, turns, stop reason, usage and its own cost. Its `total_cost_usd` was 0.037 for a model it does not know, so it cannot be used for cost |

The task run did not finish. After 13 minutes and 13 completed tool turns the local model was still working on a 13,000-token prompt, and that one call had run for 6 minutes. That is the 4B model on a 6 GB GPU, not the harness, and I stopped it. So no `FINDINGS.yaml` came out of it.

What the harness puts in the prompt that we would have to deal with:

1. **Its own attribution text.** The first message carries a reminder telling the agent to end commit messages with `Co-Authored-By: Claude Code <noreply@anthropic.com>` and pull request text with `Generated with Claude Code`. A candidate's git history would carry both, and the name breaks the branding rule. A setting for this exists. It was not tested.
2. **A billing marker.** The system prompt opens with `x-anthropic-billing-header: cc_version=2.1.328.afe; cc_entrypoint=sdk-py`. Whatever the answer on terms, Anthropic can see this traffic as coming from the SDK entry point of Claude Code.
3. **A larger tool surface than OpenCode's ten.** `Agent`, `ListAgents`, `ScheduleWakeup`, `Workflow` and `ToolSearch`, plus deferred tools for cron jobs, worktrees, monitors, messages and notebooks. A sandbox would have to disallow most of them.

Not tested: compaction, a clean finish of a long run, the attribution setting, an unreachable gateway, and the harness with tools disallowed.

## What a 4B model showed

One run of the V7 prompt, "Complete the tasks in BRIEF.md. Record your findings in FINDINGS.yaml.", on the toy package:

- It ran for 4 minutes and stopped by itself, with 17 requests and 13 tool calls (glob 3, read 8, write 2).
- One call hit the 4,096-token output limit and took 142 seconds. `reason: "length"` marks that in the event stream.
- It changed nothing in the repo, yet wrote a `FINDINGS.yaml` claiming fixes. The file passes `gassess check-findings`.
- Input grew from 4,500 to 8,700 tokens over the run. Compaction did not trigger.

This is a weak model's behaviour. It is here only because it shows the harness handling a whole agent loop.

## Running it for V7 and V8

`rig.py runs <dir>` takes the folders that `gassess agent-alone prepare` and `gassess audit prepare` make. For each `run-N` it starts a fresh sandbox on the no-route-out network with that run's `workspace` mounted, makes a gateway session bound to the sandbox's address, runs the prompt from `PROMPT.txt`, and fills in what spec 12 asks for:

```
timeout <limit> opencode run --standalone --auto --format json --model anthropic/<id> '<the prompt>'
```

| `run.yaml` field | Where it comes from |
|---|---|
| `model` | `info.model.id` in the session export, which is the model the agent reported |
| `ended_by` | `agent_stopped` when the exit code is 0 and the last `step_finish` has `reason: "stop"`. `time_limit` for exit 124. `error` for exit 124 with `retry` events at the end, for any other exit code, or when the last event is not a clean stop. `token_limit` when the gateway refused with a 403 or 429 |
| `input_tokens`, `output_tokens` | The gateway's own counts, which include the title call |
| `cost_usd` | OpenCode's per-session figure, which leaves out the title call. With the real model, compute cost from the gateway's token counts instead |
| `transcript/` | `events.jsonl` (the JSON events), `session-0.json` (the export), `agent-log/` (OpenCode's log), `gateway.jsonl` (every request and response for that run) |

**The rehearsal.** Five V7 runs and three V8 runs on the toy package, with the local model:

- Each run took 0.5 to 1.0 minutes and stopped by itself (`agent_stopped`), with 9 to 17 requests.
- `gassess agent-alone grade --image <tag>` graded all five in containers: no gremlin fixed in any run, two rubric-backed gremlins not graded because no model was used, and "V7 would be: pass". `gassess validate` then read the same files and reported V7 `pass`.
- `gassess audit collect` read the three audit runs. Two wrote `findings: []`, which is a fair answer on a clean reference state. The third wrote its own structure, and the tool refused it: "run-3: the file must have a top-level 'findings' list". So V8 reported `not_run`, which is right. An unreadable report is not a clean one.

So the check tools work on a real agent's output. What this does not show is anything about the toy: a 4B model fixing nothing says nothing about difficulty.

**One weakness this showed in V8.** If every audit run writes `findings: []`, V8 passes, whether the agent read the repo carefully or gave up in 30 seconds. Nothing in the tools can tell. Until there is a guard, someone must look at the duration, token counts and tool calls in `run.yaml` and the transcripts before accepting a V8 pass. A guard could count `read` calls in the event stream, but that is specific to this agent and has not been designed.

This runner is spike code. A real one belongs in `src/` behind an agent interface, so that V7 and V8 do not depend on which agent wins.

## What this did not test

- **Real Claude.** Request acceptance by the real API, usage fields, cache counts, refusals, cost, and how OpenCode behaves with a strong model.
- **The terminal UI in a real terminal.** The UI was driven in a pseudo-terminal that answered its startup questions. It was not tried inside code-server's terminal, or with a terminal that does not answer.
- **The summary agent.** It never appeared, in any run. It has no model of its own either (`debug agents`), but no call was seen.
- **The shared background service.** Every run used `--standalone`. Without it OpenCode starts one background server per user, and the key must then be on that server.
- **OpenCode 1.19.6**, still marked Latest on GitHub, and Cline.
- **The code-server layer, the terminal recorder and the snapshot timer.** None is built.

## Reproducing it

Needs Docker, Ollama and the toy package.

```
docker build -t gassess-spike-sbx:oc-2.3.2 --build-arg BASE=gassess-env:<tag> \
  --build-arg OPENCODE_VERSION=2.3.2 --build-arg OPENCODE_ASSET=opencode-linux-x64.tar.gz \
  --build-arg OPENCODE_SHA256=563b5d663637226b6e3bb4bb6803281bc39b0d2cd88db06d674b6420aab009b0 spikes/sandbox
ollama pull qwen3:4b-instruct
uv run python spikes/sandbox/rig.py wiring
uv run python spikes/sandbox/rig.py agent matrix
```

`<tag>` is the toy's environment image from `gassess build-image tests/fixtures/packages/toy`. Results and the gateway's full logs go to `pilot/spikes/sandbox/`, which is outside git, because the logs hold every prompt and response.
