# gremlin-assess

Internal tooling for the assessment pilot. The specs are in [docs/specs/](docs/specs/). Start with [00-decisions.md](docs/specs/00-decisions.md).

## Layout

| Path | Holds |
|---|---|
| `docs/specs/` | Specs and decision records |
| `docs/testing.md` | How to run and try everything yourself |
| `src/gremlin_assess/` | Operator-side tooling: package checks, grading, validation, sessions, reports, Gate 1a analysis |
| `src/workspace_tools/` | The small tools that ship inside a candidate workspace. Plain names only |
| `tests/` | Tests, with a toy package under `tests/fixtures/packages/toy/` |

## Setup

```
uv sync
uv run pytest                  all tests. The Docker ones skip when Docker is not running
uv run pytest -m "not docker"  only the tests that need no Docker
uv run pytest -m docker        only the Docker tests (first run pulls and builds, so it needs the network)
```

Docker Desktop must be running for anything that uses containers.

## Commands

```
uv run gassess validate <package-dir>          run the validator checks (add --containers for V2 and graded checks in Docker)
uv run gassess build-image <package-dir>       build the container image a package asks for
uv run gassess grade <package-dir> <repo-dir>  run hidden tests against a final repo (add --image TAG to use a container)
uv run gassess counts <package-dir>            print the counts table for BRIEF.md
uv run gassess export <package-dir> <dest>     build a candidate workspace
uv run gassess make-patch <base> <changed>     write a reference patch
uv run gassess check-findings <file>           check a findings file's format
uv run gassess match <package-dir> <findings>  match findings to items by location
```

Validating a package with an agent (see [spec 12](docs/specs/12-pilot-runbook.md), "Validating a package"). The tools never start an agent. They prepare folders, and read what the agent left:

```
uv run gassess audit prepare|collect <package-dir> <dir>                           V8: the unlisted-flaw audit
uv run gassess agent-alone prepare|grade <package-dir> <dir> [--image TAG] [--model]   V7: agent alone, 5 runs
```

Running a pilot (see [docs/testing.md](docs/testing.md) for a walk-through):

```
uv run gassess session new|start|note|snapshot|collect|walkthrough|check <pilot-dir> <S-001> ...
uv run gassess grade-session <pilot-dir> <S-001> <package-dir> [--image TAG] [--model]
uv run gassess packet <pilot-dir> <S-001> <package-dir> --reviewer R1 --out <dir> --forbid "Name" ...
uv run gassess review-check <package-dir> <review-form.yaml>
uv run gassess analyze <pilot-dir> --packages <dir> [--out gate1a.md]
```

Session data from the pilot is personal data. It lives outside this repo and is never committed.
