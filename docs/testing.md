# Testing guide

For running the tests yourself. Everything is run from the repo root in PowerShell. Each step says what you should see, and what to send back if you don't.

## 1. Tests that need no Docker and no key (about 1 minute)

```
uv sync
uv run pytest -m "not docker"
```

Expect 152 passed and 11 deselected, with no failures. These cover the package checks, the grading harness (it runs real hidden tests in a local process), the validator, matching, the rubric grader with a scripted fake model, sessions, packets, reviewer forms, the report and the Gate 1a arithmetic.

## 2. Docker tests (first run takes a few minutes)

1. Start Docker Desktop and wait until it says it is running. `docker info` should print a server version.
2. Run:
   ```
   uv run pytest -m docker -v
   ```
   The first run pulls `python:3.12-slim` and builds the toy image, so it needs the network. Later runs reuse both.

Expect 11 passed. What each one proves:

| Test | Proves |
|---|---|
| `test_the_container_has_no_network` | A container cannot connect out |
| `test_the_container_cannot_write_to_a_read_only_mount` | Read-only mounts really are read-only |
| `test_a_hung_container_is_killed` | A timed-out container is removed, not left running |
| `test_container_grading_matches_local_grading` | Grading in a container gives the same results as locally |
| `test_no_rig_fools_the_container_runner` (6 cases) | None of the six fake-pass tricks, including rewriting hidden test files, make a flaw look fixed |
| `test_the_validator_passes_v2_and_the_graded_checks_in_containers` | The whole validator passes in containers |

If something fails, send me the failing test's output. Things I would expect to go wrong first, because I could not run them:
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

## 5. With the real model (needs a key and costs money)

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

## 6. After a real run, the Gate 1a numbers

With sessions graded and reviewer forms saved in `pilot\reviews\` next to `assignments.csv`:

```
uv run gassess analyze $pilot --packages path\to\folder\holding\packages --out gate1a.md
```

## What is not built yet

- The pilot VM, its network allowlist to the model API, the code-server layer in the image, installing the agent (OpenCode) and the terminal recorder.
- The agent-alone runs and the unlisted-flaw audit (validator checks V7 and V8), which need the agent and a key.
- The spikes in `docs/specs/11-spike-plan.md`.
- Real problem packages. Only the toy fixture exists.
- The consent copy, data note, survey and reviewer form wording, which your co-founder drafts.
