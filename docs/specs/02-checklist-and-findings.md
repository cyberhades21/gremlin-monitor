# 02 Checklist and findings

Status: draft for Gate 0a, 2026-11-16.

## Checklist item (`hidden/checklist.yaml`)

```yaml
- id: G-001
  kind: gremlin                 # gremlin | task
  category: bug                 # bug | doc_error | test_error (gremlins only)
  subtype: logic                # label for later analytics
  contradiction: true           # the repo holds both the wrong and the right side
  root_cause_id: RC-001
  summary: "Limit check lets through orders equal to the limit"
  intended_truth: doc           # the side that is right: code | doc | test
  truth_statement: "An order at or above the limit is rejected"
  truth_evidence:
    - docs/limits.md#L10
    - tests/test_limits.py::test_reject_over_limit
  evidence_location:
    - {path: ordersvc/limits.py, lines: "40-44"}
  grading: test                 # test | rubric
  tests: [hidden/tests/items/test_g001.py]
  rubric_ref: null
  depends_on: []
```

| Field | Rule |
|---|---|
| `kind` | `gremlin` for planted flaws. `task` for feature work and the architecture explanation |
| `category` | Follows the intended truth. Code wrong is `bug`. Document or comment wrong is `doc_error`. Test wrong is `test_error`. Tasks have no category |
| `root_cause_id` | Unique per item. Several symptoms of one cause are one item |
| `contradiction` | `true` when two parts of the repo disagree and the candidate must work out which is right. Defaults to `false` |
| `truth_evidence` | Places in the repo where a careful engineer can confirm the intended truth, written as `path#L10` or `path::name`. Every gremlin needs at least one, and a contradiction needs at least two |
| `evidence_location` | Where the flaw sits in the flawed repo. Used to match findings |
| `grading` | Every item has either `tests` or a `rubric_ref`. The `rubric_ref` names the criterion that decides Fixed |
| `depends_on` | Items that must be fixed first. Empty where possible |

How often the agent alone fixed an item, was possibly misled by it, or flagged it without fixing it is not stored in the checklist. The checklist is part of the content hash, so a result written into it would change the version it describes. It is recorded in `validation/agent_alone.json` and copied into `validation/report.json` (spec 06).

**Sizing guide:** 6 to 10 scored gremlins per package, about 3 of them contradictions.

**Disclosed counts** are the number of `gremlin` items in each category. Tasks are not counted.

## Glossary (candidate-facing, copied word for word into BRIEF.md)

- **Bug.** The code does something other than what it is meant to do.
- **Documentation error.** A document or comment says something untrue about the intended behaviour, points to something that does not exist, or contradicts another document.
- **Test error.** A test that passes without checking what it claims to check, or that asserts the wrong behaviour.
- When a document and the code disagree, work out which one reflects the intended behaviour. Use the tests, the other documents and how the code is used. If the code is wrong, it is a bug. If the document is wrong, it is a documentation error.
- One underlying cause is one issue, however many places it shows up.

## Extras registry (`hidden/extras.yaml`)

Known real issues that are not on the gremlin list.

```yaml
- id: X-001
  summary: "Date parser ignores time zone"
  location: {path: ordersvc/dates.py, lines: "12-20"}
  found_by: audit               # audit | candidate
  verified_on_upstream: true
  upstream_mappable: true
  status: open                  # open | fixed_in_next_version
```

A finding that matches an extra is neutral. A new valid extra reported by a candidate is added here.

## Findings report (`FINDINGS.yaml`, filled in by the candidate)

```yaml
findings:
  - title: "Orders equal to the limit are accepted"
    category: bug               # bug | doc_error | test_error | other
    location: "ordersvc/limits.py:42"
    evidence: "docs/limits.md says 'at or above'. Reproduced with a test."
    status: fixed               # fixed | found_not_fixed
    fix_summary: "Changed > to >= and added a test"
    checked_by: "ran tests/test_limits.py"
```

- `other` is for anything outside the three counted categories.
- The submit command checks only that the file parses, every finding has all fields, and each value is allowed. It gives no hint about correctness.

## Matching findings to items

Matching decides whether an item was **identified**. Whether it was **fixed** is decided separately by hidden tests or the rubric (spec 05).

1. **By location.** A finding matches an item when its `location` is in the same file and within 10 lines of an `evidence_location`, and the categories agree. A location is a path inside the repo folder. The template's comment says so. A path that starts with the repo folder's own name is matched too, in case the candidate wrote it relative to the workspace. A finding in the right place with a different category, or with no line number, goes to step 2.
2. **By the rubric grader.** Findings left unmatched are compared with the item summaries in 3 passes. A match needs a quoted phrase from the finding in every pass.
3. **By a person.** If the passes disagree, a person decides.

| Outcome | Meaning |
|---|---|
| Matched item | The item counts as identified |
| Extra | Matches the registry, or is confirmed as a new real issue. Neutral |
| False positive | A finding in a counted category that matches nothing. Listed in the report |
| Other | Category `other`. Never a false positive |

A wrong category on a correct finding is still a match once step 2 or 3 confirms it. The report notes the mismatch.

## Acceptance checks

- Every item has a unique `id` and `root_cause_id`, and either `tests` or `rubric_ref`.
- Every contradiction item has at least two `truth_evidence` entries that exist in `public/repo/`.
- The matcher gives the same result when run twice on the same inputs for step 1.
- A findings file with a missing field is rejected by the submit command with a message that names the field.
