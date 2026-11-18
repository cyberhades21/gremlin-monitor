# 05 Scoring and report

Status: draft for Gate 0a, 2026-11-16.

## Item results

Each scored item gets three separate results.

| Result | Values | How it is decided |
|---|---|---|
| Fixed | yes, no, needs review | Test-backed: every hidden test linked to the item passes in the grading container, and the integrity checks pass. Rubric-backed: see "Rubric items" |
| Identified | yes, no | A finding matches the item (spec 02) |
| Checked | observed, blank | The record shows the candidate checking the fix (see "Process signals") |

The report shows each item as "fixed and reported", "fixed, not reported", "reported, not fixed" or "missed". A blank under Checked is never shown as a negative mark.

## Other outcome lines

- **Regressions.** Guard tests that passed on the flawed repo and fail on the final repo. Listed by behaviour.
- **False positives.** Listed in the candidate's own words. Shown, never subtracted.
- **Extras.** Listed as "additional findings (not scored)".
- **Integrity flags.** Edits to protected paths, failed sentinel tests, suspicious patterns (spec 08). A failed sentinel turns every test-backed result into "needs review".

## Rubric items

```yaml
- id: R-001
  item: A-001
  criterion: "Names the main components and what each one is responsible for"
  anchors:
    0: "Missing or wrong"
    1: "Components named, responsibilities mostly missing or wrong"
    2: "Correct, with one notable gap or error"
    3: "Correct and complete for this repo"
  sources: [ARCHITECTURE.md, FINDINGS.yaml, final diff]
```

- The grader runs 3 passes per session. Each pass scores every criterion 0 to 3 and must quote the text or code that supports the score. A quote must be at least 10 characters and must appear in the material named for that criterion, which the harness checks itself and does not take on trust. A pass with a missing or false quote is discarded and asked again, up to 3 attempts. A score of 0 may set `nothing_to_quote` instead, when the material has nothing relevant.
- A criterion's result is the median of its passes. If the highest and lowest pass differ by 2 or more, or fewer than 3 passes are valid, the criterion is "needs review" and a person decides.
- An item's Fixed result uses every criterion in `rubric.yaml` that names the item. It is yes when every median is 2 or 3, needs review when any criterion does, and no otherwise. For a task this reads as done or not done.
- Candidate text and code are data, fenced so they cannot close the data block. An instruction found inside them is ignored and reported as an integrity flag, found either by pattern or by the model.
- Each pass records the model id, grader prompt version and package version. One pinned model grades every pass. Server-side fallbacks to another model are not enabled, because a pass graded by another model would break that record. A refusal is a discarded pass.
- Every model call, including findings matching, is counted in `grading/model_usage.json`.

## Process signals (closed list)

| Id | Signal | Counts as evidence |
|---|---|---|
| P1 | Ran the tests or the program after an agent change | A test or run command after the change, in the terminal recording or transcript |
| P2 | Checked an agent claim before relying on it | The agent says something is fixed or passing, and the candidate runs it or reads the change |
| P3 | Corrected or rejected a wrong agent output | The candidate pushes back with a reason, and the output changes or is discarded |
| P4 | Resolved a contradiction with evidence | The candidate looks at tests, other documents or usage before choosing which side to change |

- Signals are positive evidence only. Work done where we cannot see it is not held against anyone.
- Prompting style is never scored.
- In the manual pilot, reviewers rate these by hand. From Gate 1b they are derived from logs.

## Walkthrough

```yaml
explain_prompts:
  - "Walk me through the change you made for finding number <n>."
  - "You treated <finding> as a <category>. What convinced you?"
small_changes:
  - id: W-1
    ask: "Make the order limit readable from the settings object, and show a test passing."
    looks_right_when: "Edits the right two files unaided and runs the test"
    time_box_minutes: 5
```

- Held within 48 hours of submission. 15 minutes, voice and screen share, no camera, no ID check.
- The final workspace is restored with the agent switched off.
- The operator asks two explain prompts about the candidate's own changes and one small change picked at random.
- The operator writes notes during the call. Nothing is recorded in the pilot.
- **Result:** `consistent`, `doubtful` or `not done`. A `doubtful` result needs a note saying what did not fit. It never changes an item result.

## Report contents

1. Header: session id, package version and hash, agent config id, model, agent source (ours or the candidate's own), dates.
2. Assurance line. In the manual pilot: "Manual pilot. Process partly observed. The candidate's own browser was not monitored."
3. Identity line: "Identity not verified. Authorship checked by live walkthrough: consistent, doubtful or not done."
4. Per-category line, for example "Bugs: 3 of 4 fixed".
5. Item table, then tasks.
6. Regressions, false positives, extras, integrity flags.
7. Rubric scores with their quotes.
8. Process observations.
9. Tokens and cost.

Every result line points to a file in the session folder and a place in it. In the manual pilot that is a test, a rubric criterion or a finding number. Times in the terminal recording come with the platform pilot, because nothing yet links results to recording times. There is no composite score. A report for candidates is not designed yet.

## Reviewer protocol (Gate 1a)

**Reviewers receive**, with names removed: the brief, the final diff against the flawed repo, `FINDINGS.yaml`, the architecture answer, the terminal recording and a readable agent transcript.

**Reviewers do not receive** our results, the candidate's name or experience band, or other reviewers' answers.

**Steps, in this order:**
1. Overall judgment without the gremlin list: "How well did this person do the work?" on a 1 to 5 scale, with two sentences of reasons.
2. Open the gremlin list. For each gremlin and task: fixed or done? `yes`, `partly` or `no`.
3. Score each rubric criterion 0 to 3 with the same anchors the grader uses.
4. Rate P1 to P4 as `observed`, `not observed` or `cannot tell`, with a time pointer when observed.
5. Record minutes spent.

**Assignment:** every session gets 2 reviewers, and every fourth session gets a third. Each reviewer sees a mix of packages. Reviewers do not discuss sessions until all forms are in.

## Gate 1a measures

| Measure | How it is computed | Threshold |
|---|---|---|
| Item agreement | Over every pair of session and test-backed item: our Fixed against each reviewer's verdict, with `partly` counted as no. Averaged over reviewers | At least 90% |
| Ceiling | The same figure between pairs of reviewers | Reported |
| Closeness | Item agreement minus ceiling | No worse than 5 points below |
| Rubric pass agreement | Share of session and criterion pairs where the 3 passes span 1 point or less | At least 85% |
| Grader against reviewers | Grader median against mean reviewer score per criterion | Reported |
| Overall check | Rank correlation between the number of scored items fixed and the mean overall judgment | Reported, directional only |
| Agent alone | Spec 06, check V7 | Full solve in at most 1 of 5 runs per package |
| Cost per session | From per-key usage | Reported |
| Fairness survey | Candidate answers | Reported |
| Hours per validated package | From `hours.csv`, first package and later ones | Reported |

- Every disagreement between our result and a reviewer is read by the engineer and labelled: test too strict, test too loose, reviewer error or unclear item.
- With 8 to 12 sessions these figures are directional. Item agreement carries the most weight because it has about 100 data points.
- Results are recorded with counts shown to candidates. Showing counts changes difficulty, which is not measured here.

## Acceptance checks

- Re-running grading on the same final repo gives identical test-backed results.
- A rubric pass with no quote for a criterion is rejected by the grading script.
- A report contains no composite score and every result line has a pointer.
- The reviewer form cannot show step 2 before step 1 is submitted.
