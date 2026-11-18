# 06 Validator

Status: draft for Gate 0a, 2026-11-16.

The validator proves a package version is sound before any candidate sees it. A version is **validated** when every check passes and the engineer has acknowledged each warning with a reason.

## Checks

| Id | Check | Passes when |
|---|---|---|
| V1 | Structure | Files match specs 01 and 02. Ids are unique. Every item has tests or a rubric reference. Counts in the brief equal counts from the checklist |
| V2 | Offline build | The image builds from pinned inputs. In a container with the network off, a probe cannot connect out, the build command succeeds, and the test command runs to completion (exit 0 or 1) and writes a readable `junit.xml` with at least one result. Every lockfile entry pins a version |
| V3 | Flawed state | On `public/repo/`, every hidden test of a test-backed gremlin fails by assertion, not by another error. A task's tests must not all pass. Every guard test passes |
| V4 | Reference state | With all reference patches applied, every hidden test and guard test passes |
| V5 | Item isolation | Applying one item's reference patch makes that item's tests pass and changes no other item's results, apart from declared `depends_on` |
| V6 | Grading integrity | Sentinel tests behave on the flawed and reference states, and the harness detects each sample of a rigged repo |
| V7 | Agent alone | See below |
| V8 | Unlisted-flaw audit | See below |
| V9 | Truth is findable | Each contradiction item has at least two `truth_evidence` entries that exist in the public repo, and the engineer has ticked each one |
| V10 | Licence | Licence is on the allowed list. The licence file matches its recorded hash. `NOTICE` is present and identical to the standard text |
| V11 | Leak scan | `public/` has no internal vocabulary, item ids, package ids or hidden paths, exactly one git commit, and no caches or backup files |
| V12 | Hours log | `hours.csv` has entries for flaw design, hidden tests, validation and approval |

## V7 Agent alone

- **Setup:** the package image, the pinned agent config and the brief exactly as a candidate gets them. `gassess agent-alone prepare` makes the workspaces, each identical to what `export` gives a candidate.
- **Prompt, word for word:** "Complete the tasks in BRIEF.md. Record your findings in FINDINGS.yaml." No follow-up messages.
- **Runs:** exactly 5. Each ends when the agent stops or reaches the session limits.
- **Passes when** all scored gremlins are fixed in at most 1 of the 5 runs.
- **Recorded per item:** how many runs fixed it, were possibly misled by it (changed the side that was already right), or flagged it without fixing.
- **Also recorded:** findings that match nothing, tokens and cost per run.

**How the check gets its data.** The validator never starts an agent, so V7 works with whichever agent spike 1 chooses. The operator, or a runner built later, runs the agent in each `run-N/workspace` and records the run in `run-N/run.yaml`: agent config, model, how it ended (`agent_stopped`, `time_limit`, `token_limit` or `error`), duration, tokens and cost. `gassess agent-alone grade` then grades every run with the same steps as a candidate session: hidden tests in a container, findings matching, and rubric items with the grading model. It writes `validation/agent_alone.json`, and V7 reads that file. The run folders stay outside the package.

V7 reports `not_run`, never `fail`, when it cannot conclude:
- What the agent saw changed after the runs were made: the public part, or the environment, session or agent settings. The runs are then stale and the agent has to run again.
- Only the hidden part or other package files changed after the runs were graded. The runs are still valid, but the grading is stale, so `gassess agent-alone grade` is run again. No new agent runs are needed.
- The runs were graded outside a container. A local run does not count (spec 08, T1).
- There are not exactly 5 runs, or a run has no `run.yaml`, names another agent config, has no `ended_by`, or ended with `error`. An errored run looks like a run that solved nothing, so it is never counted.
- A gremlin could not be graded and no other gremlin in that run is unfixed. That happens for a rubric-backed gremlin graded without a model, and for a run whose integrity check failed. Grade with `--model`, or settle each one with a reason (`agent_alone_settled`, below).

**Possibly misled.** A run was possibly misled by a contradiction when it did not fix the item and it changed a place one of the item's `truth_evidence` entries points at. That is the line for `path#L10`, the function or class for `path::name`, and any change for a bare path. It is a hint for the engineer, not a finding.

**Warnings** do not fail the check. Each has an id and stays open until the engineer accepts it with a reason (`accepted_warnings`, below). A version with an open warning is not validated.
- `V7:<item>:fixed_in_all_runs`. A gremlin fixed in all 5 runs carries no signal. Replace it or record why it stays.
- `V7:<item>:misled_in_all_runs`. A contradiction that possibly misled the agent in all 5 runs is noted for the reviewers.

## V8 Unlisted-flaw audit

- Runs on the reference state, 3 times, asking the agent to list bugs, documentation errors and test errors with locations. `gassess audit prepare` makes the workspaces: the reference state and an empty `FINDINGS.yaml`, without the brief, so the counts do not steer the agent.
- **Prompt, word for word:** "Review the repository in this folder. List every bug, documentation error and test error you can find, each with its location, in FINDINGS.yaml, and set the status of each to found_not_fixed. Do not change any other file."
- `gassess audit collect` merges the three findings files into `validation/audit.json`. Reports in the same file and within 10 lines of each other are one report. A path may be written relative to the repo or to the workspace.
- Every distinct report is triaged by the engineer. Two of the four outcomes change the reference state: fixing it in the base, and adding it to the checklist, which needs a reference patch. The audit is then run again and the report no longer appears. So only two outcomes are recorded:
  - **Extra.** An entry in `extras.yaml` in the same file and within 10 lines. A report already inside an entry needs no further decision.
  - **Rejected** as not a real issue, with a reason, under `audit_triage` in `acknowledgements.yaml`.
- Passes when no report is left untriaged. A triage entry of `extra` needs an `extras.yaml` entry near the report.
- The audit is tied to a hash of the reference state, not of the whole package. Adding an entry to `extras.yaml`, or a sign-off, leaves it valid. A change to the public repo or a reference patch makes it `not_run`, and so does a reference patch that no longer applies (fix that under V4).
- It is also `not_run` when there are not exactly 3 runs, or a run has a bad `run.yaml`, an unreadable or missing `FINDINGS.yaml`, or ended with `error`.

## Output

`validation/report.json` holds the package id, version and content hash, validator version, agent config id, each check's result, warnings with their acknowledgements, the agent-alone table, the audit table, run costs and a timestamp. It also holds the list of hidden tests seen in the reference run, which grading uses to spot missing or unexpected results.

The engineer's sign-offs live in `validation/acknowledgements.yaml`. An unknown key or a malformed entry fails V1.

```yaml
truth_confirmed: [G-001, G-003]       # contradictions whose truth evidence was checked by hand (V9)
accepted_warnings:                    # one entry per accepted warning, with a reason of at least 10 characters
  - id: "V7:G-003:fixed_in_all_runs"
    reason: "Kept on purpose as the warm-up item"
agent_alone_settled:                  # V7 results the tools could not decide
  - run: run-2
    item: G-002
    fixed: "no"                       # yes or no. An unquoted yes or no is accepted too
    reason: "The README still says cents"
audit_triage:                         # V8 decisions, one per place. A line number is required
  - location: tallybook/ledger.py:20
    decision: rejected                # extra | rejected
    note: "The empty description is allowed on purpose"   # needed for rejected
```

A check that has not run is reported as `not_run`. A version with any such check is not validated.

**Containers.** V2 and every graded check (V3 to V6) run in containers when the validator is asked to. Official validation always is. If Docker is not running, those checks report `not_run`. They never fall back to a local run. A local run exists for development only (see spec 08, T1).

## When to rerun

| Change | Rerun |
|---|---|
| Any file in the package | Every check, because it is a new version. For V7 and V8 that means grading or collecting again. New agent runs are needed only in the rows below |
| The public part, the environment, the session limits or the agent config | V7: prepare, run and grade the agent again |
| The public repo or a reference patch | V8: prepare, run and collect the audit again. V7 too, because the public part changed |
| Only the hidden part (tests, checklist, rubric, extras) | V7: `agent-alone grade` again on the same runs |
| Grading harness | V3 to V6, and `agent-alone grade` again |
| New entry in `extras.yaml` from a candidate | None. Recorded for the next version |

Do the audit before the agent-alone runs. It is cheaper, and it can lead to changes in the repo that would make the agent-alone runs stale.

## Hours per validated package

`hours.csv` has one row per work block: date, person, stage, hours, note. Stages are `flaw_design`, `hidden_tests`, `validation`, `approval` and `other`. The Gate 1a report gives the total for the first package and for later ones.

## Acceptance checks

- A package with one planted flaw removed fails V3 and names the item.
- A package whose reference solution misses one item fails V4 and names the item.
- A rigged sample repo (for example a config file that forces every test to pass) is caught by V6. In a container V6 also runs a rig that rewrites hidden test files, which must not make any item look fixed.
- A public repo containing the word "gremlin" fails V11.
- Running the validator twice on the same version gives the same results for V1 to V6 and V9 to V12.
- A package that the agent fully solves in 2 of 5 runs fails V7.
- Agent-alone runs graded before any change to the package are not counted, and neither are runs graded outside a container.
- A run that ended with an error is never counted as a run that solved nothing.
- A report left untriaged fails V8 and names its place. An `extra` decision with no `extras.yaml` entry near it fails V8.
- A version with an open warning is not validated until each warning is accepted with a reason.
