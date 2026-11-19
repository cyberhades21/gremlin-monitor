# Testing guide

For running the tests yourself. Everything is run from the repo root in PowerShell. Each step says what you should see, and what to send back if you don't.

## 1. Tests that need no Docker and no key (about 1 minute)

```
uv sync
uv run pytest -m "not docker"
```

Expect 204 passed and 12 deselected, with no failures. It takes about two minutes. 24 of those are the gateway spike's tests (`spikes/gateway/`), which use a stand-in for the model API and need no key. These cover the package checks, the grading harness (it runs real hidden tests in a local process), the validator including the agent-alone and audit checks (V7 and V8) with a pretend agent, matching, the rubric grader with a scripted fake model, sessions, packets, reviewer forms, the report and the Gate 1a arithmetic.

## 2. Docker tests (first run takes a few minutes)

1. Start Docker Desktop and wait until it says it is running. `docker info` should print a server version.
2. Run:
   ```
   uv run pytest -m docker -v
   ```
   The first run pulls `python:3.12-slim` and builds the toy image, so it needs the network. Later runs reuse both.

Expect 12 passed, in about a minute once the image is built. What each one proves:

| Test | Proves |
|---|---|
| `test_the_container_has_no_network` | A container cannot connect out |
| `test_the_container_cannot_write_to_a_read_only_mount` | Read-only mounts really are read-only |
| `test_a_hung_container_is_killed` | A timed-out container is removed, not left running |
| `test_container_grading_matches_local_grading` | Grading in a container gives the same results as locally |
| `test_no_rig_fools_the_container_runner` (6 cases) | None of the six fake-pass tricks, including rewriting hidden test files, make a flaw look fixed |
| `test_an_agent_alone_run_is_graded_in_the_container` | A run folder made by `agent-alone prepare`, which is a git checkout, is graded correctly in a container |
| `test_the_validator_passes_v2_and_the_graded_checks_in_containers` | The whole validator passes in containers |

These were run on 2026-11-19 and all 12 passed, after one fix (the toy package's test command, see the change log). If one fails on your machine, send me the failing test's output. Things that can differ between machines:
- A bind mount refused or a "permission denied" inside the container. That is the Windows file sharing setting, or the temp folder path.
- The build failing on `pip install`. That is the network.
- `test_the_container_cannot_write_to_a_read_only_mount` looking for the text "Read-only file system". The wording may differ on your Docker version.

## 3. Validate the toy package in containers

```
uv run gassess build-image tests/fixtures/packages/toy
uv run gassess validate tests/fixtures/packages/toy --containers
```

Expect V1 to V6 and V9 to V12 to pass, and V7 and V8 to say "not run" (they need the agent). The last line should say "Not validated yet".

## 4. Walk one session through by hand, no model, no Docker

This is the manual pilot in miniature. Use a scratch folder outside the repo.

```
$pkg = "tests/fixtures/packages/toy"
$pilot = "$env:TEMP\pilot-try"
$ws = "$env:TEMP\ws-try"

uv run gassess session new $pilot S-001 $pkg --candidate C-01
uv run gassess session start $pilot S-001
uv run gassess export $pkg $ws
```

Now act as the candidate. In `$ws\tallybook\tallybook\limits.py` change `return not amount > limit` to `return amount < limit`. Replace `$ws\FINDINGS.yaml` with:

```yaml
findings:
  - title: "A purchase equal to the limit is allowed"
    category: bug
    location: "tallybook/limits.py:11"
    evidence: "docs/limits.md says at or above the limit is rejected"
    status: fixed
    fix_summary: "changed the comparison to strictly below"
    checked_by: "ran the tests"
```

Write any text in `$ws\ARCHITECTURE.md`, then submit and collect:

```
uv run python -m workspace_tools.submit --workspace $ws --out "$env:TEMP\sub-try"
uv run gassess session collect $pilot S-001 (Get-ChildItem "$env:TEMP\sub-try\submission-*.tar.gz").FullName
uv run gassess grade-session $pilot S-001 $pkg
```

Open `$pilot\sessions\S-001\report.md`. Expect "G-001" to read "fixed and reported", the other planted issues "missed", rubric scores "Not graded", and the identity line to say the walkthrough is "not done". No score adds up to a total.

Then try the rest:

```
uv run gassess session walkthrough $pilot S-001 --result consistent --notes "explained the fix"
uv run gassess session check $pilot S-001 --stage collected
uv run gassess packet $pilot S-001 $pkg --reviewer R1 --out "$env:TEMP\packets-try" --forbid "Your Name"
```

`session check` lists what is missing (the terminal recording, transcript and snapshots, which you would normally supply). `packet` should write a folder with `step1` and `step2`, and should refuse if you put your own name in a transcript file and pass it with `--forbid`.

Fill a reviewer form and check it:

```
uv run gassess review-check $pkg path\to\REVIEW-step2.yaml
```

## 5. Try the audit and agent-alone checks with a pretend agent

These are the checks that stop a package being used if an agent solves it too easily (V7) or if it hides real flaws that are not on the list (V8). The tools never start an agent. You run the agent in prepared folders and they read what it left. Here you play the agent. No Docker, no key.

```
$pkg = "$env:TEMP\toy-try"
Copy-Item tests/fixtures/packages/toy $pkg -Recurse
$audit = "$env:TEMP\audit-try"
uv run gassess audit prepare $pkg $audit
```

It makes three folders `run-1` to `run-3`, each with a `workspace` (the repo in its fixed state, without the brief), a `run.yaml` to fill in, and `PROMPT.txt`. Pretend the agent finished each run, and that run 1 reported one problem:

```
1..3 | ForEach-Object { Set-Content "$audit\run-$_\run.yaml" "agent_config: ac-test`nended_by: agent_stopped`n" }
@"
findings:
  - title: "The README example prints nothing useful"
    category: doc_error
    location: "README.md:5"
    evidence: "the usage block has no output"
    status: found_not_fixed
    fix_summary: ""
    checked_by: ""
"@ | Set-Content "$audit\run-1\workspace\FINDINGS.yaml"
uv run gassess audit collect $pkg $audit
```

Expect "U-001 README.md:5 runs 1" and "V8 would be: fail", saying the report is not triaged. Triage it by adding this to `$pkg\validation\acknowledgements.yaml`, under the existing line:

```yaml
audit_triage:
  - location: README.md:5
    decision: rejected
    note: "The example is meant to be minimal"
```

Run `uv run gassess validate $pkg --only V1 V8` and expect V8 `pass`.

For V7, make five runs the same way (`gassess agent-alone prepare $pkg "$env:TEMP\runs-try"`), fill in each `run.yaml`, and pretend the agent fixed things by applying reference patches into a run's repo, for example `git -C "$env:TEMP\runs-try\run-2\workspace\tallybook" apply "$pkg\hidden\reference\G-001.patch"`. Then `gassess agent-alone grade $pkg "$env:TEMP\runs-try"`. Without `--image` it says the grading does not count, because V7 only counts runs graded in a container. With Docker running, add `--image` and the tag from `gassess build-image`. Fixing every gremlin in two runs fails V7. A gremlin fixed in all five runs gives a warning that needs a reason in `acknowledgements.yaml` (`accepted_warnings`). `docs/specs/06-validator.md` has the exact formats.

## 6. With the real model (needs a key and costs money)

Use a key from the dedicated pilot workspace with a low spend limit.

```
uv sync --extra model
$env:ANTHROPIC_API_KEY = "<the key>"
uv run gassess grade-session $pilot S-001 $pkg --model
```

Check `report.md`: the rubric section should show three passes per criterion with quotes you can find in the candidate's files, and `grading/model_usage.json` should show the tokens used. The cost is small for the toy package, but look at the key's usage afterwards.

I could not test this part, so these are the likeliest problems:
- **The API rejects the answer format** (an error mentioning the schema). The schemas are `SCHEMA` in `src/gremlin_assess/grading/rubric.py` and `src/gremlin_assess/grading/findings_llm.py`.
- **Passes keep being discarded.** `grading/rubric.json` lists the reasons under `discarded`. Usually the model quoted text that is not in the material.
- **A refusal.** It shows as "the model declined to answer" in `discarded`.

Send me the error text or the `discarded` list.

## 7. After a real run, the Gate 1a numbers

With sessions graded and reviewer forms saved in `pilot\reviews\` next to `assignments.csv`:

```
uv run gassess analyze $pilot --packages path\to\folder\holding\packages --out gate1a.md
```

## What is not built yet

- The pilot VM, its network allowlist to the model API, the code-server layer in the image, installing the agent (OpenCode) and the terminal recorder.
- The agent that makes the runs for V7 and V8. The checks themselves are built and read the runs you record, but no agent is installed or scripted yet. That waits for the agent choice (spike 1).
- The spikes in `docs/specs/11-spike-plan.md`.
- Real problem packages. Only the toy fixture exists.
- The consent copy, data note, survey and reviewer form wording, which your co-founder drafts.
