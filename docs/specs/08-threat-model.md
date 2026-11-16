# 08 Threat model

Status: draft for Gate 0a, 2026-11-16. This draft covers content and grading. Platform threats (session token, sandbox escape, log tampering) are added after the spikes.

## Who we defend against

- A candidate who wants a better result than their work deserves.
- The agent, which may recognise a repo or invent findings.
- Our own mistakes in authoring, which can leak answers or punish a correct candidate.

## Threats and defences

| Id | Threat | Defence | How we test it |
|---|---|---|---|
| T1 | Candidate code rigs the hidden-test run: a config file or plugin that forces passes, patched test tooling, a fake results file | Hidden tests live outside the candidate's tree and run with our own config. Candidate config files and plugins are not loaded. Plugin autoloading is off. Results are written where candidate code cannot reach. Sentinel tests must fail and must pass as expected. Runs have a timeout and no network | Validator V6 runs a set of rigged sample repos |
| T2 | Candidate edits test infrastructure to hide a failure | Edits to `protected_paths` are flagged in the report. Guard tests catch broken behaviour | A sample session that edits `pytest.ini` produces a flag |
| T3 | Candidate text tries to instruct the rubric grader | Candidate content is passed as data. Each score needs a quote. 3 passes. An instruction found in the content is reported as a flag | A sample answer containing "give full marks" scores on its content and raises a flag |
| T4 | Findings report lists many guesses to hit matches | Identification needs a location and evidence. Unmatched findings in counted categories are listed as false positives | A sample report with 20 guesses shows the false positives |
| T5 | The agent recognises the base repo and spots the planted change | Niche repos, the upstream name kept out of the public part, agent-alone runs, and retiring items it always solves | Validator V7 |
| T6 | The agent invents a finding to reach the disclosed count | Measured in V7 as findings that match nothing. Candidates who accept one get a false positive | Validator V7 |
| T7 | A real bug outside the list makes a correct candidate look wrong | Audit before release, the extras registry, and extras being neutral | Validator V8 |
| T8 | Licence notices mark where flaws were planted | MIT and BSD only in the pilot. One standard `NOTICE` with no file list | Validator V10 |
| T9 | The public repo leaks the answer through git history, caches, backup files or internal names | Single-commit export and a leak scan | Validator V11 |
| T10 | Hidden material reaches a candidate | Hidden files are never copied into a candidate container. Hidden test names and output never appear in anything a candidate sees | Search of a running candidate container |
| T11 | Pilot volunteers share a package | Volunteers are asked not to. Each candidate gets a package they have not seen. Variants come later | Not testable. Accepted for the pilot |
| T12 | A reviewer is influenced by our result or by who the candidate is | Reviewers get no results, names or experience bands, and give an overall judgment before seeing the gremlin list | The reviewer packet is checked against the list in spec 05 |

## Residual risks we accept for the pilot

- **Targeted test rigging.** Candidate code is imported during grading, so code written with knowledge of a specific hidden test could fake that one result. Candidates never see hidden tests, and sentinels catch blanket tricks.
- **A second AI in another tab.** A browser cannot prevent it. Reports state the assurance level.
- **Package sharing between volunteers.** See T11.
- **Hostile code on our host.** Only invited volunteers take part until the hardened runtime exists.

## Acceptance checks

- Every row with a test in the last column has that test in the validator or in the pilot dry run.
- The rigged sample set for T1 has at least these cases: a forced-pass config file, a plugin loaded from the candidate tree, a patched assertion, a pre-written results file, and a process that exits early with success.
