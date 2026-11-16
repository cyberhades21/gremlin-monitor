# 12 Manual pilot runbook

Status: draft for Gate 0a, 2026-11-16.

In the manual pilot the operator is the platform. This runbook makes every session run the same way.

## Roles

| Role | Who | Does |
|---|---|---|
| Operator | Engineer | Launches sessions, collects artifacts, runs grading, runs walkthroughs |
| Coordinator | Co-founder | Recruits, schedules, sends consent forms and surveys, sends reviewer packets, keeps the tracker |
| Reviewer | 3-4 experienced engineers | Judge sessions blind using the form in spec 05 |

## Identifiers

- Sessions are `S-001`, `S-002` and so on. Candidates are `C-01`, `C-02`.
- The only file linking a candidate code to a person is `candidates.csv`. It is kept outside the git repo and outside reviewer packets.

## Where data lives

All session data stays on the pilot VM and one encrypted backup. None of it is committed to git.

```
pilot/
  sessions/S-001/
    meta.yaml         session, candidate code, package version, agent config,
                      agent source, key id, start, end, interventions
    terminal.cast     terminal recording
    transcript/       agent session logs, as the agent wrote them
    snapshots/        workspace snapshot every 10 minutes
    final/            final repo
    FINDINGS.yaml
    grading/          test results, rubric passes, flags
    walkthrough.md    notes and result
    report.md
  reviews/            exported reviewer forms
  tracker.csv         session status
```

## Before each session

Two days ahead:
1. Coordinator confirms the signed consent form and the candidate's public SSH key.
2. Operator assigns a package the candidate has not seen, keeping packages balanced.
3. Operator creates one API key in the pilot workspace and checks the spend limit.
4. Coordinator sends joining instructions and the session time.

At the start:
1. Launch the container from the package image and inject the key.
2. Start the terminal recorder and the snapshot timer.
3. From inside the container, confirm a request to a public site fails and a request to the model API succeeds.
4. Tell the candidate to connect, then start the timer.

## During the session

- The operator is reachable by chat for logistics only.
- For any question about the task, the answer is: "I can't comment on the task. Use your judgment and note your assumptions in your findings."
- Every intervention is written into `meta.yaml` with the time.

| Problem | Action |
|---|---|
| Agent or model outage over 10 minutes | Pause the timer and log it. If it does not recover, stop, discard the session and reschedule with a different package |
| Candidate loses connection | The timer keeps running. One pause of up to 10 minutes is allowed, and is logged |
| Candidate asks for more time | No |

## At submit or timeout

1. Stop the agent and take a final snapshot.
2. Copy the artifacts into `pilot/sessions/<id>/`.
3. Revoke the API key and record its usage and cost in `meta.yaml`.
4. Stop the container. Keep it for the walkthrough, then delete it.

## Own-agent sessions

At most 3 of 12 sessions, chosen before the session starts, Claude Code only until OpenAI's terms are read.

- The unmodified Claude Code CLI is installed. The candidate signs in. The operator never sees or handles the credentials.
- The allowlist gains the sign-in and API hosts for that vendor.
- At the end, copy the transcript files first, then delete the agent's credential and configuration directories before any other artifact is collected.
- `meta.yaml` records `agent_source: own-claude-code`. These sessions are left out of the cost and agent-alone figures.

## After the session

1. Run the grading container on `final/`. Run the 3 rubric passes. Build `report.md`.
2. Coordinator schedules the walkthrough within 48 hours and sends the fairness survey.
3. Operator builds the reviewer packet (list in spec 05) and checks the transcript and recording for the candidate's name. Coordinator sends it.
4. Operator runs the walkthrough from the script in spec 05 and writes `walkthrough.md`.
5. Update `tracker.csv` and the hours log.

## Data rules

- Raw session data is deleted 90 days after the Gate 1a report, unless the candidate agreed to longer.
- Reviewers delete their packets once their forms are submitted.
- A candidate can withdraw at any time. Their session data is then deleted and the tracker notes the withdrawal.

## What the consent copy must state

For the coordinator to draft and the engineer to review:
- What is recorded: the terminal, the conversation with the assistant, the code, walkthrough notes, survey answers and an experience band.
- Why: to test a grading method. It is not a hiring decision.
- Who sees it: the two founders, and reviewers who get it without the candidate's name.
- That prompts and code are sent to an AI model provider outside India.
- How long it is kept and how to withdraw.
- For own-agent sessions: that the login stays under the candidate's control and is deleted at the end.
- A request not to share the task with others.

## One-time setup checklist

- [ ] Pilot VM reachable by SSH keys only, with a firewall.
- [ ] Pilot workspace created with a spend limit.
- [ ] Package 1 validated (spec 06).
- [ ] Full dry run by the operator: launch, work the problem, submit, grade, build a reviewer packet, run a mock walkthrough.
- [ ] Consent form, data note, survey and reviewer form approved.
- [ ] Reviewer assignment table filled in.

## Acceptance checks

- A second person can run a mock session from this runbook without asking the operator a question.
- After a mock session, every file in the artifact list exists and the API key no longer works.
- The reviewer packet from the mock session contains no name, no result of ours and no experience band.
