# Spike 1, desk check: which agent do candidates get

Status: desk check, 2026-11-19. Nothing in this note was run. Later the same day OpenCode and the Agent SDK harness were run behind the gateway against a local model, and several unknowns below were answered. See `01-opencode-hands-on.md`. The facts come from primary pages (repo files, official docs) that helper readers fetched and summarised, so each claim has a confidence mark. Hands-on testing decides. This note says what to test and in what order.

Marks: **yes** is documented. **partly** is documented with a gap. **no** is documented as not available. **unknown** was not found or not read.

## Result in brief

1. **Test OpenCode first**, pinned to one exact version, in the 3-hour hands-on box. It is MIT licensed, very active, can be pointed at our own endpoint, and raises no terms question. Its docs are thin exactly where we care most: which models it calls besides the main one, telemetry, and session export. It also has two major versions with different config keys. Those gaps are the hands-on tests.
2. **The Agent SDK harness is the best documented option and the runner-up.** Nearly everything we need has a documented switch, and the transcript is complete. One thing sits outside our control: Anthropic's written answer on how we use it (see "Terms").
3. **Cline is third.** It has no tool allow list and still contacts its own servers with telemetry off.
4. **Drop Kilo Code.** Its CLI is a fork of OpenCode with product telemetry on by default and Kilo's own gateway as the default provider. It adds nothing we need.
5. **A harness we write on the Messages API was not on the list.** It is the fallback if OpenCode and the SDK both fail. The price is a weaker agent, and that changes what the assessment measures.

Whichever agent wins, the model gateway does the work that matters most: it pins the model by rejecting every other, counts tokens, and keeps the full request log. That makes spike 5 independent of the agent choice, so it can start now ("What the gateway must handle").

## The checklist from spec 11

| # | Check | OpenCode | Agent SDK harness | Cline | Kilo CLI |
|---|---|---|---|---|---|
| 1 | Custom base URL | partly. Key names differ between major versions | yes. `ANTHROPIC_BASE_URL` | partly. `cline auth -b`, not confirmed for Anthropic | no for Anthropic. A custom provider with an "Anthropic Messages" type exists |
| 2 | Session token as the API key | yes. `{env:ANTHROPIC_API_KEY}` in config | yes. `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | yes. `ANTHROPIC_API_KEY`, and keys are also saved in `providers.json` | yes. `{env:VAR}`, global config only |
| 3 | Streaming and tool calls through a proxy | unknown | yes. The gateway protocol is documented | unknown | unknown |
| 4 | Model pin, including other models | partly. Main model yes. Hidden title, summary and compaction agents: model not documented | partly. Env vars for main, background and subagent models, but no promise of one model only | unknown | unknown |
| 5 | Tool restriction, web off | partly. Permission rules, deny all then allow. A shell command has the host's authority | yes. `disallowed_tools`, no settings files loaded. "A permission gate, not a sandbox" | no. "No allow or deny list for shell commands" | unknown |
| 6 | Telemetry and cloud features off | partly. An update switch is documented. Telemetry is not mentioned either way | yes. Four switches. Two calls still go straight to api.anthropic.com | partly. An opt-out setting, but it still contacts Cline's servers for sign-in and feature flags | partly. Opt-out exists, on by default, default provider needs a Kilo sign-in |
| 7 | Log completeness | partly. SQLite database, no export documented | yes. JSONL with every prompt, tool call, result and response | partly. Session folders, a SQLite index, HTML export | unknown |
| 8 | Licence | MIT | The package is under Anthropic's Commercial Terms. The Claude Code binary inside it is not open licensed | Apache-2.0 | MIT |
| 9 | Provider terms | Ordinary API use with our key | See "Terms" | Ordinary API use with our key | Ordinary API use with our key |

## OpenCode

- **Which project.** `sst/opencode` now redirects to `anomalyco/opencode`. Not the archived Go project that became Crush. MIT, "Copyright (c) 2025 opencode", with no exceptions in the LICENSE file. About 228k stars, commits daily.
- **Two major versions at once.** GitHub marks v1.19.6 (17 Nov) as Latest, and v2.3.2 (18 Nov) is newer. The docs on opencode.ai describe version 2, whose install paths contain `/v2/`. Version 2 changes how permissions are written (a `permission` setting became a `permissions` list, and the tool names `bash` and `task` became `shell` and `subagent`), and the agents page says its session runner "does not yet" send some per-agent request settings. Pin one exact version. Never use `latest`.
- **Config keys (one reader of the version 2 docs, not confirmed by a second):** `providers.anthropic.settings.baseURL`, `apiKey: "{env:ANTHROPIC_API_KEY}"`, top-level `"model": "anthropic/<model>"`, permission rules `{action, resource, effect}` where the last matching rule wins and the base policy allows everything, `"update": "disable"` (global config only), `"websearch": false`, and a documented policy that denies every provider except one.
- **Managed config.** A system config directory (`/etc/opencode` on Linux) is documented as overriding user and project config. If that holds, a root-owned locked config cannot be weakened from the candidate's account. Test it.
- **Shared server.** By default it starts one shared background server per user, and the API key must be set on that server. `opencode --standalone` gives a private one.
- **Headless.** `opencode run "<prompt>"` is documented, with `--model provider/model`. The readable pages list no JSON output flag, no session flag and no directory flag for `run`. Test what exists.
- **Transcript.** A SQLite database at `~/.local/share/opencode/opencode.db` (`OPENCODE_DB` moves it) and a log under `~/.local/share/opencode/log/`. No export command is documented. Copying the data directory at the end of a session is the fallback.
- **Other calls it can make:** a models.dev catalog fetch, update checks, web search providers, MCP sign-in metadata, remote instruction and skill URLs. None has a documented off switch except update. With our network rule they fail closed, so the test is that startup survives that.
- **Not found at all** (pages returned 404, or the topic is absent): any policy or privacy page, `small_model` in version 2, a config path variable such as `OPENCODE_CONFIG`, proxy variable support, and an API reference.

## Agent SDK harness (the SDK runs the Claude Code binary)

Read from the official docs at code.claude.com, mostly verbatim.

- **Endpoint and keys.** `ANTHROPIC_BASE_URL` sends sampling requests to our gateway. `ANTHROPIC_API_KEY` is sent as `X-Api-Key`. The trap: set a gateway credential as well, or a subscription login may be used instead.
- **Models.** `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` (also used for background tasks) and `CLAUDE_CODE_SUBAGENT_MODEL`. The docs do not promise that only one model id is ever requested, so pin by rejection at the gateway and check `modelUsage` in tests.
- **Tools.** `allowed_tools` only auto-approves and does not restrict. `disallowed_tools` removes a tool (`WebSearch`, `WebFetch`, `Bash`, `Agent`). `strict_mcp_config`, `setting_sources=[]` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` keep anything from the host out.
- **Telemetry.** `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `DISABLE_AUTOUPDATER`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`. Two checks still go to api.anthropic.com directly and not through the base URL: a WebFetch domain check and a fast-mode check. Our network rule blocks both. Test that this is harmless.
- **Transcript.** `~/.claude/projects/<encoded-cwd>/*.jsonl` "contains your prompt, every tool call the agent made, every tool result, and every response", in plain text. `CLAUDE_CONFIG_DIR` moves it.
- **Front end.** `ClaudeSDKClient` takes streaming input and gives streaming output, so we can write the terminal chat front end. Python 3.10 or later. The binary is bundled, and the spawned CLI needs no separate Node.js "for most installs".
- **Hosting guidance** from Anthropic matches our design: keep credentials out of the agent and inject them at a proxy, run with `--cap-drop ALL` and `--read-only`, and `--network none` with a proxy reached over a Unix socket. About 1 GiB of memory and 1 CPU per agent.

## Terms for the Agent SDK (read on the primary pages, 2026-11-19)

- Agent SDK overview, "License and terms": use of the SDK "is governed by Anthropic's Commercial Terms of Service, including when you use it to power products and services that you make available to your own customers and end users".
- Legal page, "Authentication and credential use": developers building products, "including those using the Agent SDK, should use API key authentication through Claude Console or a supported cloud provider", and may not offer claude.ai login or route requests through subscription credentials.
- Legal page, "Can customers offer Claude Code in their products?": for **Claude Code**, "Customers may not pay for, resell, or intermediate Claude usage on their end users' behalf. Each end user must authenticate with their own Anthropic API key, Claude subscription plan credentials, or 3P inference provider credential." A carve-out lets customers provision their own API keys "for use by the customer's own authorized users", with usage billed to the key owner.
- **What the pages do not say:** whether that Claude Code rule also covers a product built on the Agent SDK. No page says the two are treated alike, and the SDK page describes itself as "the same tools, agent loop, and context management that power Claude Code".
- **So:** D15 holds. The Claude Code CLI is not the product agent. An SDK harness on our key is documented as allowed, but our case (we pay for the candidate's usage) is the one the CLI rule worries about. The written answer from Anthropic sales, already on the co-founder's list, should name the SDK explicitly.
- **Branding.** The assistant must not be called "Claude Code" or "Claude Code Agent". Allowed: "Claude Agent", or "{YourAgentName} Powered by Claude". The candidate UI and the brief need a name that follows this.

## Cline

Apache-2.0, not archived, a CLI that runs headless (`npm i -g cline`, `--json`, `--yolo`). Against it: no allow or deny list for shell commands. The documented workaround is a hook that cancels `run_commands`. Telemetry off still leaves sign-in and feature-flag calls to Cline's servers, with no single switch for cloud features. Provider settings, including API keys, are stored in `~/.cline/data/settings/providers.json`. The base URL for Anthropic and an environment variable for it are not documented.

## Kilo Code

MIT, active. The README and CLI docs say "Kilo CLI is a fork of OpenCode". Product telemetry is on by default (`KILO_TELEMETRY_LEVEL=off` turns it off). The default provider needs a Kilo sign-in, and the docs footer says Kilo "has been acquired by Anaconda". It brings OpenCode's gaps plus a vendor relationship. Dropped.

## What the gateway must handle (spike 5, from the SDK's gateway protocol)

These are the details any gateway that sits in front of Anthropic's API has to get right. They apply to OpenCode as well, because both end up calling the same API.

- Paths: `POST /v1/messages` (also with `?beta=true`), optionally `POST /v1/messages/count_tokens`, and a `HEAD /api/hello` connection probe. Refuse everything else.
- Forward `anthropic-version` and `anthropic-beta` unchanged, and never allow-list individual beta values.
- Never rewrite request bodies. Inspect them only. Rewriting breaks header and body pairs and causes 400 errors.
- Do not buffer streamed responses (`text/event-stream`). A buffering gateway makes the agent stall.
- Pass error bodies and retry headers (`retry-after`, `x-should-retry`) through unchanged.
- Our additions: swap the session token for the real key, reject a request whose `model` differs from the pin, count tokens from the usage fields, refuse after the cap, and write every request and response to a log.

## Hands-on box for OpenCode (3 hours, needs Docker and a key)

Pass all of these to choose it. Fail the third or fourth and move to the SDK harness.

1. Install a pinned version in the image from a checksum-verified file, not `curl | bash` at run time. Record `opencode --version`.
2. Write the locked config in the root-owned managed location: our gateway as base URL, the model pinned, deny all then allow what is needed, update disabled, no MCP, no plugins, no web tools. Confirm a candidate-owned config file cannot override it.
3. Run it headless with `opencode run` in a workspace. Record how it ends, whether there is a JSON or event output, and how to read the whole session afterwards. V7 and V8 need this.
4. With the container able to reach only the gateway, check that it starts without hanging, and read the gateway log: only `/v1/messages`, only the pinned model. Look at what the title, summary and compaction steps request.
5. Get a readable transcript out of the data directory.
6. Check that the per-session key from the environment is what the gateway sees.

## Limits of this desk check

- The readers summarised pages. Several decisive points rest on one reader. Treat every "reported" claim as a hypothesis for the hands-on test.
- Six opencode.ai pages returned 404 (policies, tools, api, migrate-v1, config and troubleshooting without the `/docs/` prefix). The site looks mid-migration, so the docs may not match any release exactly.
- Cline's and Kilo's model pinning, proxy behaviour and logging were not read. They only matter if the first two options fail.
- Cost, quality and the feel of each agent for a candidate were not assessed. That needs hands-on time.
- The Agent SDK terms question is for Anthropic to answer, not for this note.

## Sources (read 2026-11-19)

- OpenCode: https://github.com/anomalyco/opencode (LICENSE, README, releases, commits), https://opencode.ai/docs/ and its config, providers, cli, permissions, agents and troubleshooting pages.
- Cline: https://github.com/cline/cline (LICENSE, CLI README), https://docs.cline.bot (cli, provider-config, telemetry, config).
- Kilo Code: https://github.com/Kilo-Org/kilocode (LICENSE, README, releases, PRIVACY.md), https://kilo.ai/docs (cli, providers, product telemetry).
- Agent SDK and Claude Code: https://code.claude.com/docs/en/agent-sdk/ (overview, python, permissions, sessions, hosting, secure-deployment, cost-tracking), /env-vars, /llm-gateway, /llm-gateway-protocol, /data-usage, /legal-and-compliance.
