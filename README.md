# gremlin-monitor

A browser VS Code (code-server) in a container, loaded with a repo. First slice of the interview
sandbox: give the candidate the link and password.

## Run

```
docker compose up --build
```

This starts three services: `wakapi` (stores the activity events), `wakapi-setup` (runs once, makes the
Wakapi user and key) and `editor`. Open http://127.0.0.1:8181 and sign in with the password printed in the
log (`Editor password: ...`). By default it clones https://github.com/fastapi/full-stack-fastapi-template.

## Use another repo

The image holds no repo. The repo is cloned when the container first starts, so switch like this:

```
docker compose down
REPO_URL=https://github.com/owner/repo docker compose up
```

In PowerShell use `$env:REPO_URL = "https://github.com/owner/repo"` first, or put the values in a `.env`
file (see `.env.example`). `REPO_REF` picks a branch or commit, `PASSWORD` sets the password, `PORT`
changes the port. `docker compose down` throws away the candidate's work (the activity data in Wakapi is
kept); a plain restart keeps everything. `docker compose down -v` also wipes the Wakapi data.

## What is inside

code-server 4.145.2, git, ripgrep, Python 3.14 (via uv), uv, bun. Telemetry, update checks and the AI
chat panel are off. The template's backend tests also need Postgres, which this image does not have yet.

Extensions common to every repo are listed in `extensions.txt` (one `publisher.name@version` per line,
from Open VSX, which is the only marketplace code-server uses). Edit the file and run
`docker compose build`; the build fails if an extension cannot be installed. Editor settings are in
`settings.json`. Pylance is Microsoft-only, so Python code navigation comes from basedpyright.

## Activity monitoring

The WakaTime extension reports which file the candidate has open, the cursor line, the file's line count,
saves, and how many lines a person typed (`human_line_changes`) to our own Wakapi. No file contents,
terminal commands or searches are sent. (Each heartbeat also lists the module names a file imports; Wakapi
receives them but does not store them.) The candidate can see the extension and can change its settings,
so treat the data as evidence that something happened, not proof that something did not.

- **Read it:** the Wakapi dashboard is at http://127.0.0.1:3001 (login: `docker compose logs wakapi-setup`).
  Through the API, with the key from `docker compose exec editor cat /shared/wakapi.key`:
  `GET /api/compat/wakatime/v1/users/current/heartbeats?date=YYYY-MM-DD` with
  `Authorization: Basic <base64 of the key>`.
- **Sessions:** each editor reports under its machine name, which is `SESSION_ID` (default `editor`).
- **End of a session:** run `docker compose exec editor wakatime-cli --sync-offline-activity 0` to send the
  command-line tool's queue. The extension holds the newest events in memory until its next event, so the
  last half minute of a session can be missing.
- **AI lines:** `ai_line_changes` stays 0 for our setup. The extension only detects AI from in-editor AI
  features (switched off here), and an agent editing files from the terminal is not counted as AI.
- **Without Wakapi** (for example a plain `docker run`), the extension is switched off in `~/.wakatime.cfg`
  and stays quiet. The command-line tool is pinned in the Dockerfile (checked against GitHub's published
  SHA-256), so nothing is downloaded or updated while a session runs.
- **Before real use:** set your own `WAKAPI_PASSWORD_SALT` in `.env`, and tell candidates what is recorded.
