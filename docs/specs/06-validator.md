# 06 Validator

Status: draft for Gate 0a, 2026-11-16.

The validator proves a package version is sound before any candidate sees it. A version is **validated** when every check passes and the engineer has acknowledged each warning with a reason.

## Checks

| Id | Check | Passes when |
|---|---|---|
| V1 | Structure | Files match specs 01 and 02. Ids are unique. Every item has tests or a rubric reference. Counts in the brief equal counts from the checklist |
| V2 | Offline build | The image builds from pinned inputs. With the network disabled, the build and public tests run |
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

- **Setup:** the package image, the pinned agent config and the brief exactly as a candidate gets them.
- **Prompt, word for word:** "Complete the tasks in BRIEF.md. Record your findings in FINDINGS.yaml." No follow-up messages.
- **Runs:** 5. Each ends when the agent stops or reaches the session limits.
- **Passes when** all scored gremlins are fixed in at most 1 of the 5 runs.
- **Recorded per item:** how many runs fixed it, were misled by it (changed the side that was already right), or flagged it without fixing.
- **Also recorded:** findings that match nothing, tokens and cost per run.

**Warnings:**
- A gremlin fixed in all 5 runs carries no signal. Replace it or record why it stays.
- A contradiction that misleads the agent in all 5 runs is noted for the reviewers.

## V8 Unlisted-flaw audit

- Runs on the reference state, 3 times, asking the agent to list bugs, documentation errors and test errors with locations.
- Every distinct report is triaged by the engineer: fix it in the base, add it to the checklist, add it to `extras.yaml`, or reject it as not a real issue.
- Passes when no report is left untriaged.

## Output

`validation/report.json` holds the package id, version and content hash, validator version, agent config id, each check's result, warnings with their acknowledgements, the agent-alone table, run costs and a timestamp. It also holds the list of hidden tests seen in the reference run, which grading uses to spot missing or unexpected results.

The engineer's sign-offs live in `validation/acknowledgements.yaml`: the contradiction items whose truth evidence has been checked by hand, and the reason for each accepted warning.

A check that has not run is reported as `not_run`. A version with any such check is not validated.

## When to rerun

| Change | Rerun |
|---|---|
| Any file in the package | Everything. It is a new version |
| Agent config | V7 and V8 |
| Grading harness | V3 to V6 |
| New entry in `extras.yaml` from a candidate | None. Recorded for the next version |

## Hours per validated package

`hours.csv` has one row per work block: date, person, stage, hours, note. Stages are `flaw_design`, `hidden_tests`, `validation`, `approval` and `other`. The Gate 1a report gives the total for the first package and for later ones.

## Acceptance checks

- A package with one planted flaw removed fails V3 and names the item.
- A package whose reference solution misses one item fails V4 and names the item.
- A rigged sample repo (for example a config file that forces every test to pass) is caught by V6.
- A public repo containing the word "gremlin" fails V11.
- Running the validator twice on the same version gives the same results for V1 to V6 and V9 to V12.
