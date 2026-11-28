# gremlin-monitor

A browser VS Code (code-server) in a container, loaded with a repo. First slice of the interview
sandbox: give the candidate the link and password.

## Run

```
docker compose up --build
```

This starts seven services:

- `wakapi-init` and `wakapi-setup` run once: a random salt for Wakapi, then this session's own Wakapi user
  and key.
- `wakapi` stores the activity events.
- `ingest` is the editor's only door to Wakapi (see "Keeping sessions apart").
- `egress` is the editor's only way to the internet (see "Outside traffic").
- `editor` is the code-server with the repo.
- `edge` is the front door that shows the consent page first (see "The consent page").

Open http://127.0.0.1:8181. The candidate sees the consent page, then the editor's sign-in, where the password
is the one printed in the log (`Editor password: ...`). By default it clones
https://github.com/fastapi/full-stack-fastapi-template.

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

- Put `PASSWORD` in `.env` rather than on the command line. Any later `docker compose` command that recreates
  the editor without it makes a new random password.
- Use an `https://` URL. The host in it is allowed through the proxy automatically; ssh URLs cannot be cloned.
- Do not put a token in the URL. The candidate can read it (`git remote -v`).

## What is inside

code-server 4.145.2, git, ripgrep, Python 3.14 (via uv), uv, bun. Telemetry, update checks and the AI
chat panel are off. Packages install (pip, uv, npm, bun) through the outbound proxy. The template's backend
tests also need Postgres, which this image does not have yet.

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

- **Read it:** the Wakapi dashboard is at http://127.0.0.1:3001, and each session has its own login
  (user `s-<SESSION_ID>`). It is in `docker compose logs wakapi-setup` after the first start; later, read it with
  `docker compose run --rm --no-deps --entrypoint cat wakapi-setup /shared/operator/<SESSION_ID>.login`.
  Through the API, use that session's key (`/shared/sessions/<SESSION_ID>/wakapi.key`, read the same way):
  `GET /api/compat/wakatime/v1/users/current/heartbeats?date=YYYY-MM-DD` with
  `Authorization: Basic <base64 of the key>`.
- **Sessions:** each session is one Wakapi user, `s-<SESSION_ID>` (default session name `editor`; lowercase
  letters, digits and dashes). The editor also reports under that name as its machine name. To add a session's
  user and key on its own: `docker compose run --rm --no-deps -e SESSION_ID=two wakapi-setup`.
- **End of a session:** run `docker compose exec editor wakatime-cli --sync-offline-activity 0` to send the
  command-line tool's queue. The extension holds the newest events in memory until its next event, so the
  last half minute of a session can be missing.
- **AI lines:** `ai_line_changes` stays 0 for our setup. The extension only detects AI from in-editor AI
  features (switched off here), and an agent editing files from the terminal is not counted as AI.
- **Without Wakapi** (for example a plain `docker run`), the extension is switched off in `~/.wakatime.cfg`
  and stays quiet. The command-line tool is pinned in the Dockerfile (checked against GitHub's published
  SHA-256), so nothing is downloaded or updated while a session runs.
- **Wakapi's password salt** is made at the first start by `wakapi-init` and kept in a volume
  (`wakapi-config`), so there is nothing to set. It survives `down` and `up`; `down -v` makes a new one along
  with new data. If you ran an earlier version (a fixed salt, or one shared Wakapi user), run
  `docker compose down -v` once.
- **Before real use:** have the consent text approved (see below).

## Keeping sessions apart

Each session has its own Wakapi user and API key, and the editor is only given its own key: the shared volume
is mounted at `sessions/<SESSION_ID>`, so it cannot see another session's key or any dashboard login (those
are in `operator/`). The candidate can read their own key, so the key alone is not a secret from them.

**Wakapi does not keep keys apart by itself.** Tested on Wakapi 2.18.2: a valid key for one user could read
another user's heartbeats, and write into their record, just by naming that user in the URL
(`/users/s-two/...`). What keeps the sessions apart is the door, `ingest` (a Caddy server, `ingest.Caddyfile`):

- Wakapi is not on the editor's network. Its dashboard, sign-up and settings (which can relay or import data
  to and from addresses the user types) are out of the candidate's reach.
- The door passes exactly two requests: `POST /api/users/current/heartbeats.bulk` (and `.../heartbeats`) to send
  events, and `GET /api/users/current/statusbar/today` for the coding-time counter in the status bar. In those,
  `current` means whoever owns the key, so a request can only touch the sender's own record. Naming another
  user, any other path, other methods, and bodies over 1 MB are refused.
- **Do not add another route from the editor to Wakapi.** Without the door, any key can read and write any
  user.

Checked from inside the editor, as a candidate: sign-up, login, settings, reading data and every path trick I
tried (dot segments, encoded slashes, other prefixes, a named user, other methods) were refused or stayed in
the sender's own record, and the real extension in a browser kept working through the door.

What this does not stop: a candidate can still send made-up events into their own record with their own key.
Treat the data as evidence that something happened, not proof that something did not.

## The consent page

Before anything else, the front door (`edge`, a Caddy server configured in `Caddyfile`) shows the candidate
`consent.html`: what is recorded, what is not, that outside sites are blocked, and a checkbox. The editor's
sign-in cannot be reached until they accept, and the choice is remembered in a cookie until the browser is
closed. Each acceptance is written to a log with the time, the session name (`SESSION_ID`) and the version of
the text:

```
docker compose exec edge cat /var/log/gremlin/consent.log
```

- **The text is a first version.** The company running the interview, and a lawyer, need to approve it before
  real candidates see it, and it needs how long the record is kept and who else can see it. Raise `v=1` in the
  form in `consent.html` whenever the wording changes, so the log shows which text each candidate accepted.
- **Keep it true.** It describes what is recorded in "Activity monitoring". If that changes, change the text.
- **The log's address is Docker's.** It records the address the request came from, which behind Docker is the
  gateway (172.x), not the candidate's. When the link is opened to other machines, set up the front door to
  pass the real address on.

## Outside traffic

The editor has no route to the internet: it sits on a Docker network marked `internal`. Its only way out is
the `egress` proxy (Squid), which lets through the sites in `egress-allowlist.txt` (GitHub, PyPI, npm and Yarn)
plus the host the repo is cloned from, and refuses everything else. The proxy keeps no record of what it is
asked for. Edit the list and run `docker compose up -d egress` to apply it.

Checked from inside the editor, as a candidate would: curl without the proxy and to a raw IP address fail,
DNS lookups fail, a site that is not on the list gets a 403, and `git ls-remote`, `uv pip install` and curl to
the listed sites work.

- **GitHub is on the list.** It is needed to clone, and it also hosts the project's own history and every other
  public repo, so a candidate can read them from the terminal.
- **Only the editor is covered.** The candidate's own browser, phone and other tabs are theirs.
- **Tools that need other sites fail** (OS packages, container images). Add the site to the list.
