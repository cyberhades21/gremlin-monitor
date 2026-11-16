# gremlin-assess

Internal tooling for the assessment pilot. The specs are in [docs/specs/](docs/specs/). Start with [00-decisions.md](docs/specs/00-decisions.md).

## Layout

| Path | Holds |
|---|---|
| `docs/specs/` | Specs and decision records |
| `src/gremlin_assess/` | Operator-side tooling: package checks, grading, validation |
| `src/workspace_tools/` | The small tools that ship inside a candidate workspace. Plain names only |
| `tests/` | Tests, with a toy package under `tests/fixtures/packages/toy/` |

## Setup

```
uv sync
uv run pytest
```

## Commands

```
uv run gassess validate <package-dir>          run the validator checks
uv run gassess grade <package-dir> <repo-dir>  run hidden tests against a final repo
uv run gassess counts <package-dir>            print the counts table for BRIEF.md
uv run gassess export <package-dir> <dest>     build a candidate workspace
uv run gassess make-patch <base> <changed>     write a reference patch
uv run gassess check-findings <file>           check a findings file's format
uv run gassess match <package-dir> <findings>  match findings to items by location
```

Session data from the pilot is personal data. It lives outside this repo and is never committed.
