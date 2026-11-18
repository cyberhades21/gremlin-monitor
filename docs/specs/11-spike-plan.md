# 11 Spike plan

Status: draft for Gate 0a, 2026-11-16.

A spike is a short, throwaway experiment that answers one question. Spike code lives in `spikes/` and is never promoted without a rewrite.

## Rules

- Each spike stops at its hour box, finished or not.
- Each spike ends in a one-page decision record with measured numbers, saved in `docs/specs/decisions/`.
- Spikes fill the waiting time in the manual pilot. They never delay a pilot session.

## Spikes

| # | Question | Method | Hours | Earliest start |
|---|---|---|---|---|
| 1 | Which agent harness do candidates get at Gate 1b? | Desk check of every option against the checklist below, then hands-on with the best three | 10 | After pilot tooling works |
| 2 | Which browser editor, and is one needed at Gate 1b? | Lock down code-server and OpenVSCode Server and compare. Use pilot experience of stock code-server | 4 | After the first pilot sessions |
| 3 | Can the agent run outside the candidate container? | Paper design, then one test with the leading agent | 4 | After spike 1 |
| 4 | Can the process signals in spec 05 be derived without an editor extension? | Derive P1 to P4 from pilot transcripts, recordings and snapshots. Compare with reviewers' hand ratings | 5 | After 6 pilot sessions are reviewed |
| 5 | Can a thin gateway do what Gate 1b needs? | Build a minimal proxy and run the leading agent through it | 7 | Any time |

## Spike 1 options and checklist

**Options:** OpenCode, stock Cline, a minimal fork of Cline, Kilo Code, and a thin custom harness on the Agent SDK.

- Claude Code CLI is listed as "needs written agreement" and is not tested unless Anthropic agrees.
- Roo Code is excluded because its repo is archived.
- **Desk check done, 2026-11-19** (`docs/spikes/01-agent-desk-check.md`). Hands-on order: OpenCode, then the Agent SDK harness, then Cline. Kilo Code is dropped, because its CLI is a fork of OpenCode with product telemetry on by default and a vendor gateway as the default. A harness we write on the Messages API is added as the last fallback.
- The gateway (spike 5) is the same for every option, so it can start before this spike's hands-on part.

**Check each option for:**
1. Custom base URL.
2. A per-session token accepted as the API key.
3. Streaming and tool calls passing through a proxy unchanged.
4. Model pinning, including any secondary model the harness calls.
5. Tool restriction, with web tools off.
6. Vendor telemetry and cloud features switched off.
7. Log completeness: every prompt, response, tool call and file edit recoverable.
8. Licence, read from the LICENSE file in the repo.
9. Terms of the model provider for this use.

**If a fork is chosen:** rename it, keep the licence notices, pin the version, and merge only security fixes at first.

## Exit criteria

| # | The decision record states |
|---|---|
| 1 | The chosen harness, the runner-up, and the checklist result for each option |
| 2 | The chosen editor, memory per session, and whether the extensions view and built-in AI features can be fully disabled. Or the simpler setup that replaces it |
| 3 | Whether a sidecar harness is workable, and what it would cost to build |
| 4 | For each of P1 to P4: derivable or not, and agreement with hand ratings. If any is not derivable, what an extension would need to capture |
| 5 | Token swap, cap, pin by rejection, full logging, binding to the sandbox address and the fingerprint flag each shown working or not. Any refusal or fallback model is visible per call |

## Moved to Phase 2

- Java generality.
- Hardened runtime.
- Production hosting choice.

## Acceptance checks

- Every decision record has numbers that were measured, with the command or method used.
- Total spike time stays within 30 hours.
