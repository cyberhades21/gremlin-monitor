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

## 8. Try a real agent in a sandbox with a local model (Docker and Ollama, no key, no cost)

This is the spike rig in `spikes/sandbox/`. It puts a sandbox on a Docker network with no route out, with the gateway as its only way to a model, and your Ollama behind the gateway. The agent is OpenCode 2.3.2. The write-up is `docs/spikes/01-opencode-hands-on.md`. A small local model is far weaker than Claude, so this checks plumbing and what the agent does, not how hard a problem is.

```
ollama pull qwen3:4b-instruct
uv run gassess build-image tests/fixtures/packages/toy        # prints the image tag, call it <tag>
docker build -t gassess-spike-sbx:oc-2.3.2 --build-arg BASE=<tag> --build-arg OPENCODE_VERSION=2.3.2 `
  --build-arg OPENCODE_ASSET=opencode-linux-x64.tar.gz `
  --build-arg OPENCODE_SHA256=563b5d663637226b6e3bb4bb6803281bc39b0d2cd88db06d674b6420aab009b0 spikes/sandbox
uv run python spikes/sandbox/rig.py wiring          # expect: 18 of 18 checks passed
uv run python spikes/sandbox/rig.py agent matrix    # expect: six variants, each exit 0, 2 requests, mcp started False, tools 10
```

The first call loads the model, so `wiring` takes a minute or two. Both commands clean up their containers and networks. If one is interrupted, `uv run python spikes/sandbox/rig.py down` removes them. Results and the gateway's logs go to `pilot/spikes/sandbox/`, which is outside git, because the logs hold every prompt and response.

Other experiments, all `uv run python spikes/sandbox/rig.py agent <name>`:

| Name | What it shows |
|---|---|
| `probe` | Startup with no route out, and one tiny prompt |
| `override` | Candidate-owned config against the locked one, with a control run without the lock |
| `matrix` | The hardened lock against each kind of widening on its own, then all together |
| `bisect` | Which managed deny rule breaks an untouched agent (it was `integration.use`) |
| `prod-shape` | The request shape for the real `claude-opus-5-5` id. It makes a temporary local alias of that name in Ollama and removes it afterwards |
| `task` | The V7 prompt, for up to `--minutes` |
| `compaction` | A tiny declared context window, to see the hidden compaction call |
| `tui` | The interactive terminal UI, driven in a pseudo-terminal |
| `sdk`, `sdk-task` | The Agent SDK harness. Needs a second image: `docker build -t gassess-spike-sdk:0.2.185 --build-arg BASE=<tag> --build-arg SDK_VERSION=0.2.185 -f spikes/sandbox/Dockerfile.sdk spikes/sandbox` |

`sdk-task` does not finish with a 4B model on a small GPU, which is expected. Only one rig can run at a time, because each one removes the containers of the last.

To rehearse V7 and V8 with this agent, make the run folders as in section 5 on a copy of the toy package, then fill them with `uv run python spikes/sandbox/rig.py runs <runs-dir> --minutes 8` and grade with `gassess agent-alone grade <pkg> <runs-dir> --image <tag>`. Expect V7 `pass` (a small model fixes nothing) and V8 `not_run` or `pass`, depending on what the agent wrote.

## 9. Try a candidate session in your browser (Docker and Ollama, no key, no cost)

This is the nearest thing to what a candidate sees: a browser editor, a terminal with the assistant in it, and a `submit` command. The model is the same small local one as in section 8, so the assistant is weak. This is about how it feels and whether the plumbing holds. The write-up is `docs/spikes/02-browser-editor.md`.

```
uv run python spikes/sandbox/rig.py build-editor     # once. Needs the network for a 237 MB download, and the OpenCode image from section 8
uv run python spikes/sandbox/rig.py editor
```

It runs eight checks inside the sandbox (expect `Self-check: 8 of 8 passed`), then prints an address and a password. Open the address in a browser **on the same machine**. It listens on `127.0.0.1` only, so another device cannot reach it.

What to try, in this order:

1. Log in. You should see an empty editor with `BRIEF.md`, `FINDINGS.yaml` and `tallybook` in the file list. There should be no chat panel and no welcome page.
2. Read `BRIEF.md` as a candidate would. Open `tallybook/docs/limits.md` and `tallybook/tallybook/limits.py` and spot where they disagree.
3. Open a terminal with Ctrl+` and type `opencode`. Ask it something small, such as "Explain tallybook/tallybook/limits.py in one sentence". Expect short answers and a slow first one while the model loads.
4. Change a file in the editor. It saves by itself.
5. Fill in `FINDINGS.yaml` (the comments at the top explain each field), then type `submit` in the terminal. Expect `Submitted: submission-....tar.gz`. If the file is malformed it says what to fix.
6. Refresh the page. The terminal and the assistant should still be there.
7. Try to get out, as a curious candidate would: `curl https://example.com` should fail, the Extensions view should offer nothing to install, and `touch /etc/opencode/opencode.json` should say the file system is read-only.

To end, press any key in the PowerShell window that is running the command, or press Ctrl+C. It removes the containers and copies the logs out. Everything is in `pilot\spikes\sandbox\editor-<time>\`, which is outside git because it holds every prompt and answer: `workspace`, `submissions`, `editor.log`, `self-check.json`, `requests.json` and `gateway\`.

Grade what you submitted with the same commands as section 4:

```
$run = "pilot\spikes\sandbox\editor-<time>"          # the folder the command named
$pilot = "$env:TEMP\pilot-editor"
uv run gassess session new $pilot S-001 tests/fixtures/packages/toy --candidate C-01
uv run gassess session start $pilot S-001
uv run gassess session collect $pilot S-001 (Get-ChildItem "$run\submissions\submission-*.tar.gz" | Select-Object -First 1).FullName
uv run gassess grade-session $pilot S-001 tests/fixtures/packages/toy
```

Open `$pilot\sessions\S-001\report.md`. If you reported the limit bug, G-001 reads "reported, not fixed".

Things you will notice:
- `FINDINGS.yaml` has a `!` icon. That is the editor's icon for YAML files, not an error.
- The model's token lasts 60 minutes (`--minutes`) and stops at 300 requests (`--max-requests`). After that the assistant says it cannot reach the model. Start a new session.
- One session at a time. `uv run python spikes/sandbox/rig.py down` cleans up after an interrupted run.
- If port 18443 is busy, add `--port 18500`.
- Only Microsoft Edge was tried. Other browsers should behave the same but were not checked.
- Send me the `editor.log` and the `self-check.json` from the run folder if anything looks wrong.

## What is not built yet

- The pilot VM, its network allowlist to the model API, the terminal recorder and the snapshot timer. OpenCode and a browser editor are installed in spike images and run through the gateway (sections 8 and 9), but nothing here has run against the real model API.
- A runner for V7 and V8 that is not spike code. `rig.py runs` fills the run folders for OpenCode, and a real one belongs in `src/` behind an agent interface.
- The remaining spikes in `docs/specs/11-spike-plan.md`. Spike 1's hands-on part is done for OpenCode only, and against a local model.
- Real problem packages. Only the toy fixture exists.
- The consent copy, data note, survey and reviewer form wording, which your co-founder drafts.
