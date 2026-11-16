# 01 Problem package

Status: draft for Gate 0a, 2026-11-16.

A package is one self-contained problem. Everything needed to run a session, grade it and prove the problem is sound lives in the package.

## Layout

```
<package-id>/
  package.yaml          metadata, never shown to candidates
  public/               the only part a candidate ever receives
    repo/               the flawed repo, exported clean
    BRIEF.md            tasks, counts, glossary, rules
    FINDINGS.yaml       empty template the candidate fills in
  hidden/
    checklist.yaml      the gremlin list and task items (spec 02)
    extras.yaml         known issues outside the list (spec 02)
    rubric.yaml         criteria for rubric-graded items (spec 05)
    walkthrough.yaml    questions and small changes (spec 05)
    tests/items/        hidden tests, one group per item
    tests/guards/       hidden tests for behaviour that must keep working
    reference/          the reference solution, one patch per item
  validation/
    report.json         written by the validator (spec 06)
    hours.csv           time spent on this package, by stage
```

## package.yaml

```yaml
id: pkg-0001
version: 1.0.0
content_hash: sha256:...        # set by the validator
candidate_name: ordersvc        # plain name shown to candidates
language: python
upstream:
  url: https://...              # recorded here only
  commit: <sha>
  licence: MIT                  # MIT, BSD-2-Clause or BSD-3-Clause in the pilot
  licence_file: public/repo/LICENSE
  licence_file_sha256: ...
session:
  time_limit_minutes: 90
  tasks: [find_and_fix, change_feature, add_feature, explain_architecture]
agent_config: ac-2026-11-a
protected_paths: [pytest.ini, pyproject.toml, conftest.py]
environment:
  toolchain: python-3.12
  base_image: <name>@sha256:...
  system_packages: [{name: git, version: "<exact>"}]
  dependencies:
    lockfile: public/repo/requirements.lock
    cache: {path: /opt/wheels, sha256: ...}
  build_command: null
  test_command: "pytest -q"
  results_format: junit-xml
  editor_extensions: []         # each: {id, version, sha256, source: open-vsx}
  resources: {cpu: 2, memory: 4g}
  network: none
image_digest: sha256:...        # set by the image builder
```

`session.tasks` and `time_limit_minutes` are set per package.

## Rules

1. **Hidden stays hidden.** No file under `hidden/`, `validation/` or `package.yaml` is ever copied into a candidate container.
2. **Counts are derived.** The counts table in `BRIEF.md` is generated from `checklist.yaml`. It is never typed by hand.
3. **Versions are immutable.** Once validated, a version never changes. Its `content_hash` is a SHA-256 over a sorted list of file hashes.
4. **Offline.** The image builds from pinned inputs, and the build and tests run with the network disabled.
5. **Clean export.** `public/repo/` is stored as plain files with no git data, caches, compiled files or editor backups. The export step gives the candidate's copy a git history of exactly one commit. Nothing in it can reveal what was changed.
6. **Plain names.** Nothing under `public/` contains internal vocabulary, package ids or item ids.
7. **One result format.** The test command writes JUnit XML.
8. **Pinned extensions.** Each editor extension is listed with its version and hash and is installed at image build time only.

## BRIEF.md must contain

- One paragraph on what the project does.
- The tasks and the time limit.
- The counts table: scored issues per category.
- This sentence, word for word: "The counts above are the issues we score. If you find anything else, list it under 'other'. It will not count for or against you."
- The glossary from spec 02, word for word.
- This sentence, word for word: "The assistant can be wrong. Check its work the way you would check a colleague's."
- How to submit, and a pointer to what is recorded.

## Licence notices

- The upstream licence file ships unchanged in `public/repo/LICENSE`. Its hash is recorded in `package.yaml`.
- `public/repo/NOTICE` holds one sentence: "This copy has been modified from the original work." The file is identical in every package and lists no files.
- No per-file notices in the pilot. An Apache-2.0 base later needs the same header on every source file, so that notices cannot mark where flaws were planted.
- The upstream URL and commit are recorded in `package.yaml` only.
- A lawyer reviews this handling before the first paying customer.

## Agent config

One platform-level file per config, referenced by id.

```yaml
id: ac-2026-11-a
harness: {name: opencode, version: "<exact>"}
model: claude-opus-5-5
effort: <set explicitly and recorded>
tools: {file_read: on, file_edit: on, shell: on, web: off}
limits: {session_tokens: <set after the dry run>, session_minutes: 90}
telemetry: off
```

Any change makes a new id. Every session and every validation run records the id it used.

## Format check against a second language

To check the format is not Python-shaped, one Java environment was written by hand:

```yaml
environment:
  toolchain: java-17
  system_packages: [{name: maven, version: "<exact>"}]
  dependencies:
    lockfile: public/repo/pom.xml
    cache: {path: /opt/m2, sha256: ...}
  build_command: "mvn -o -q compile"
  test_command: "mvn -o -q test"
  results_format: junit-xml
  resources: {cpu: 2, memory: 6g}
  network: none
```

It needed two things the first draft lacked: a separate `build_command`, and a `cache.path` for pre-fetched dependencies. Both are now in the schema. Nothing has been built or run for Java.

## Acceptance checks

- A package directory passes the validator's structure check (spec 06, V1).
- The counts in `BRIEF.md` equal the counts computed from `checklist.yaml`.
- A search of a running candidate container finds no hidden file, item id or package id.
- With the network disabled, the image runs `build_command` and `test_command` to completion.
- `git log` in an exported candidate repo shows exactly one commit, and two exports of one version give the same commit.
