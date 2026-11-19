# 00 Decisions and vocabulary

Status: draft for Gate 0a, 2026-11-16.

A decision here changes only with the engineer's sign-off, recorded as a dated line in the change log at the bottom.

## Vocabulary

| Term | Meaning |
|---|---|
| Gremlin | A flaw planted in a repo on purpose |
| Gremlin list | The hidden checklist of scored items for a package |
| Gremlin farm | The later tool that turns one base project into many validated variants |
| Package | One problem: flawed repo, brief, hidden tests, checklist, rubric, reference solution, metadata |
| Package version | An immutable, validated snapshot of a package, identified by a content hash |
| Scored item | A checklist entry that appears in the report: a gremlin or a task |
| Extra | A real issue outside the gremlin list. Neutral for the candidate |
| Session | One candidate working on one package version |
| Operator | The person who runs sessions by hand in the manual pilot |
| Reviewer | An experienced engineer who judges sessions blind |
| Agent config | The pinned harness, model, settings, tools and limits every candidate gets |
| Grading container | A clean container where hidden tests run. Candidates never reach it |
| Gate | A sign-off point: 0a (first specs), 1a (manual pilot), 1b (platform pilot) |

Internal words such as "gremlin" never appear in anything a candidate sees.

## Decisions

### Product

- **D1** All flaws live in the repo: bugs, contradictions between code, comments, documents and architecture notes, and tests that pass for the wrong reason. Nothing is injected into model output.
- **D2** Candidates see the number of scored issues per category and a glossary. Only counted issues are scored. Anything else they report counts neither for nor against them.
- **D3** One scored item per root cause, however many symptoms it has.
- **D4** Every contradiction has one intended truth, fixed in the package, that a careful engineer can confirm from the repo.
- **D5** The report is a per-item breakdown with links to the session record. There is no composite score. An internal tally exists only to compare our results with reviewers.
- **D6** Process signals come from a closed list and count only as positive evidence. "Not observed" never lowers a result.
- **D7** Every session is followed by a short live walkthrough. Its result is a flag, not a score. The report says "Identity not verified. Authorship checked by live walkthrough."
- **D8** Candidates are told the assistant can be wrong.
- **D9** Candidate-facing repos and files use plain names.

### Content

- **D10** Base repos are niche open-source Python projects of about 100-200 stars, tested with pytest. Pilot licences are MIT and BSD only.
- **D11** Claude proposes each flaw and hidden test. The engineer approves each one.
- **D12** A validated package version is immutable. Any change makes a new version.
- **D13** Hours per validated package are tracked from the first package, by stage.

### Agent and model

- **D14** Every candidate gets the same agent config. The model is Claude Opus 5.5 (`claude-opus-5-5`). Sonnet 5.5 is the fallback if measured cost is too high.
- **D15** Claude Code CLI is not the product agent unless Anthropic agrees in writing. The agent is an open-source harness or an Agent SDK harness on our own API key.
- **D16** In the manual pilot, at most 3 of 12 sessions may use the candidate's own Claude login in the unmodified Claude Code CLI. Those sessions are labelled.

### Platform

- **D17** The gateway never changes model output. It swaps in the real key, enforces a token cap and the pinned model, logs requests and responses, and may reject a request.
- **D18** From Gate 1b no real key exists in any sandbox. In the manual pilot each session has its own key with a low spend limit, revoked at submit.
- **D19** The session token is assumed readable by the candidate. Theft is made harmless by network binding, the cap, the pin, expiry and logging.
- **D20** The editor is stock code-server or OpenVSCode Server. Microsoft's VS Code Server and the Microsoft Marketplace are never used. Extensions come from Open VSX at image build time, pinned by version and hash.
- **D21** No custom editor extension to start. One is built only if a spike shows a required signal cannot be derived without it.
- **D22** Backend code is Python. Production hosting is chosen in Phase 2, behind a sandbox-provider interface.

### Process

- **D23** Work is clean-room. Nothing is copied or adapted from the Paxel script or its prompts.
- **D24** No session runs without signed consent and a data note. Retention and deletion rules apply from the first session.
- **D25** Only invited volunteers use the system until the hardened runtime exists.
- **D26** Commercial validation is outside this plan.

## Open questions

- Anthropic's written answer on Claude Code CLI in the product, and confirmation of Agent SDK use on our API key.
- OpenAI's terms for Codex with a candidate's own login.
- The bonus rule for valid extras, after Gate 1a.
- An optional derived summary for HR, after the pilot.
- A "bring your own agent" mode, after the pilot.
- The effect of showing counts on difficulty.
- Apache-2.0 base repos, which need the same notice on every file.

## Change log

- 2026-11-19: spike 1 hands-on, first part, in `docs/spikes/01-opencode-hands-on.md`. OpenCode 2.3.2 ran in a sandbox with no route out, through the gateway, in front of a local Ollama model (no Anthropic key). It passes all six checks in the hands-on box. No decision changes. Findings: the base URL needs `/v1` on the end; one gateway pin covers every call, because the title call uses the session's model; denying the `integration.use` policy breaks the agent; OpenCode's own cost figure leaves out the title call; an unreachable provider is retried for ever. A root-owned `/etc/opencode/opencode.json` beats every candidate-owned config. V7 and V8 were rehearsed end to end with this agent. Not shown: anything about real Claude, the interactive UI, or cost.
- 2026-11-19: the Docker tests were run for the first time: 11 of 12 passed. The twelfth, the whole validator in containers, failed at V2 because the toy fixture's `test_command` was a bare `pytest`, which cannot import the repo's own code from the repo root. The check was right and the fixture was wrong. The toy and spec 01's example now use `python -m pytest`, and spec 01 rule 7 says why.
- 2026-11-19: spike 1 desk check written (`docs/spikes/01-agent-desk-check.md`). Hands-on order: OpenCode, the Agent SDK harness, Cline. Kilo Code dropped. D15 checked against the primary pages and kept: the rule against paying for end users' usage is written for the Claude Code CLI, and the Agent SDK is stated to be governed by the Commercial Terms "including when you use it to power products and services that you make available to your own customers and end users", with API-key authentication. The pages do not say the CLI rule also covers the SDK, so the written answer from Anthropic should name the SDK. New constraint from Anthropic's branding rules: the assistant must not be called "Claude Code" or "Claude Code Agent". "{Name} Powered by Claude" is allowed.
- 2026-11-19: validator checks V7 (agent alone) and V8 (unlisted-flaw audit) built. Without them no package could ever be validated. The validator never starts an agent: it prepares run folders, the operator runs the pinned agent in them, and it reads what was left, so the checks work with whichever agent spike 1 chooses. Spec 06 gains the run folders, `run.yaml`, when each check is `not_run` and not `fail`, warnings that must be accepted with a reason, and the formats in `acknowledgements.yaml`. Spec 06 also separates what the agent saw (public part, environment, session and agent settings) from the hidden part, so editing `extras.yaml` during the audit means grading the V7 runs again and not running the agent again. Of the four V8 triage outcomes only two (extra, rejected) can be recorded, because the other two change the reference state and force a new audit. Spec 02 loses the `agent_alone` checklist field, since writing results into the checklist would change the hash of the version they describe. Spec 12 gains "Validating a package". Not yet run against a real agent or a container.
- 2026-11-16: first draft, from plan revision 2.
- 2026-11-16: Gate 0a signed off by the engineer.
- 2026-11-18: the rest of the pilot tooling written: rubric grader and model-assisted findings matching, session folders, report, reviewer packets and forms, Gate 1a analysis. Spec 05 gains the rubric details above (verified quotes, how several criteria combine, no model fallbacks) and says pointers are file references in the manual pilot. Spec 12 gains the folder layout and command order. The reviewer form loader accepts an unquoted yes or no, which YAML reads as true or false. Nothing here has been run against a real model or Docker.
- 2026-11-18: container grading and V2 added. Spec 01 gains the `junit.xml` convention, quoted hashes, image and wheel-cache rules. Spec 06 says V2 and the graded checks run in containers and never fall back to a local run. Spec 08 records that a local grading run can be fooled by rewritten hidden tests, so only container runs count. Code written and unit-tested, but the real-Docker tests have not run yet because Docker Desktop was off.
- 2026-11-16: clarifications found while building the tooling. Spec 02 gains the `contradiction` field, says what `rubric_ref` decides, and says a right-place finding with the wrong category goes to step 2. Spec 01 says the single commit is made at export. Spec 06 tightens V3 and names `acknowledgements.yaml`.
