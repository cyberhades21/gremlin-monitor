# gremlin-monitor

A browser VS Code (code-server) in a container, loaded with a repo, with a console for the interviewer:
pick a project, get a link for the candidate, and read back what they did.

## Interviewer console

```
docker compose build            # once, and again after changing the editor image
python interviewer/app.py       # Python 3.11 or newer on this computer; nothing to install
```

Open http://localhost:8400 and sign in with the password printed on the first start
(`python interviewer/app.py --new-password` makes a new one). Use `localhost`, not `127.0.0.1`: browsers keep
cookies per host name, so this keeps your sign-in away from the editors, which candidates control.

- **New session:** pick a project, give it a label, and wait until it says Ready (a few minutes the first
  time, while the project's packages install). The page shows the link and editor password to send.
- **A session** shows consent, the activity timeline and the submitted changes. **End session** takes a final
  `submit`, copies the record to this computer and stops the containers. **Delete** removes everything.
- Each session is its own copy of `compose.yaml` (project name `gremlin-<session>`), so sessions share
  nothing but the editor image. Records are kept in `interviewer/data/`, which is not in git.
- **Links work only on this computer** (`127.0.0.1`). Reaching a candidate elsewhere needs HTTPS and a host,
  which is not built.

### Projects

The catalogue is `interviewer/catalog.json`. Each project has a repository, the commit every candidate gets
(`ref`), a `difficulty` (easy, medium, hard, extra-hard or uncategorized), and optionally databases
(`services`: `postgres`, `mongodb`), settings for the editor (`env`) and a preparation command (`setup`).

| Project | Needs | Tests run in a fresh session |
|---|---|---|
| Full Stack FastAPI Template | PostgreSQL | 58 passed |
| Spring PetClinic REST | nothing (in-memory database) | 321 passed (`./mvnw test`) |
| Node Express Boilerplate | MongoDB | 113 passed (`yarn test`) |

To add a project, add an entry. A repository on another host is allowed through the proxy automatically; a
package registry that is not in `egress-allowlist.txt` has to be added there.

## Run one session by hand

```
docker compose up --build
```

This starts eight services:

- `wakapi-init` and `wakapi-setup` run once: a random salt for Wakapi, then this session's own Wakapi user
  and key.
- `wakapi` stores the activity events.
- `collector` keeps the changes handed in with `submit` (see "Submitting").
- `ingest` is the editor's only door to both (see "Keeping sessions apart").
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
changes the port. `docker compose down` throws away the candidate's work, so run `submit` first (the submitted
changes and the activity data in Wakapi are kept); a plain restart keeps everything. `docker compose down -v`
also wipes the submissions and the Wakapi data.

- Put `PASSWORD` in `.env` rather than on the command line. Any later `docker compose` command that recreates
  the editor without it makes a new random password.
- Use an `https://` URL. The host in it is allowed through the proxy automatically; ssh URLs cannot be cloned.
- Do not put a token in the URL. The candidate can read it (`git remote -v`).

## What is inside

code-server 4.145.2, git, ripgrep, Python 3.14 (via uv), uv, bun, Java 21, Maven 3.9, Node 22, npm and yarn.
Telemetry, update checks and the AI chat panel are off. Packages install (pip, uv, npm, yarn, bun, Maven)
through the outbound proxy; Java gets it through `JAVA_TOOL_OPTIONS` and `~/.m2/settings.xml`, so Java
programs print "Picked up JAVA_TOOL_OPTIONS". Databases are started per session, only for projects that ask.

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
- **Events arrive late, by up to about three minutes.** In a test session the delay was 1 to 167 seconds
  (typically over a minute): the extension holds events until its next one, and the command-line tool queues
  them. So the end of a session is only complete after `submit` (see "Submitting"). `docker compose down`
  without it can lose the last few minutes.
- **What the numbers do not mean** (seen in that test session):
  - Pasted lines count as typed: a 30-line paste showed as 31 typed lines. "Typed" is the net change in lines,
    so deleting counts as negative.
  - Changes made from the terminal, by a script or by an agent, are not recorded, nor are commands, searches,
    git operations or scrolling.
  - A file that is renamed or deleted soon after leaves no trace of the earlier work in it, and creating a new
    file and typing in it left none when it was renamed shortly after.
  - The branch name is read when the event is sent, not when it happened, so it can be wrong around a branch
    switch.
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

## Submitting

When the candidate is done they type `submit` in the editor's terminal. The interviewer can do the same:

```
docker compose exec editor submit
```

It saves everything changed since the project was cloned (their commits, uncommitted edits, new and deleted
files) as one diff, then sends the last of the activity record, which takes about half a minute. The browser
window has to still be open for that last part. Submitting again saves another copy; nothing is overwritten.

Read the submissions (they survive `docker compose down`, not `down -v`):

```
docker compose cp collector:/submissions ./logs/submissions
```

Each file starts with a short header (session, time, starting commit, files changed) and can be applied to a
fresh clone of that commit with `git apply`.

- **Not in the diff:** files the project ignores (installed packages, caches) and the contents of binary files.
  Changes larger than 5 MB are refused, and `submit` says so.
- **It is evidence, not proof.** The candidate controls their environment and could hand in something other
  than what is in the folder.
- **How it gets out:** `submit` sends it through `ingest` with the session's key, to `collector`
  (`collector.py`), which the editor cannot otherwise reach and cannot read back from.

## Keeping sessions apart

Each session has its own Wakapi user and API key, and the editor is only given its own key: the shared volume
is mounted at `sessions/<SESSION_ID>`, so it cannot see another session's key or any dashboard login (those
are in `operator/`). The candidate can read their own key, so the key alone is not a secret from them.

**Wakapi does not keep keys apart by itself.** Tested on Wakapi 2.18.2: a valid key for one user could read
another user's heartbeats, and write into their record, just by naming that user in the URL
(`/users/s-two/...`). What keeps the sessions apart is the door, `ingest` (a Caddy server, `ingest.Caddyfile`):

- Wakapi is not on the editor's network. Its dashboard, sign-up and settings (which can relay or import data
  to and from addresses the user types) are out of the candidate's reach.
- The door passes exactly three requests: `POST /api/users/current/heartbeats.bulk` (and `.../heartbeats`) to
  send events, `GET /api/users/current/statusbar/today` for the coding-time counter in the status bar, and
  `POST /submit` to hand in changes. `current` means whoever owns the key, and a submission is filed under the
  session whose key it carries, so a request can only touch the sender's own record. Naming another user, any
  other path, other methods, and oversized bodies are refused.
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
the `egress` proxy (Squid), which lets through the sites in `egress-allowlist.txt` (GitHub, PyPI, npm, Yarn and
Maven Central)
plus the host the repo is cloned from, and refuses everything else. The proxy keeps no record of what it is
asked for. Edit the list and run `docker compose up -d egress` to apply it.

Checked from inside the editor, as a candidate would: curl without the proxy and to a raw IP address fail,
DNS lookups fail, a site that is not on the list gets a 403, and `git ls-remote`, `uv pip install` and curl to
the listed sites work.

- **GitHub is on the list.** It is needed to clone, and it also hosts the project's own history and every other
  public repo, so a candidate can read them from the terminal.
- **Only the editor is covered.** The candidate's own browser, phone and other tabs are theirs.
- **Tools that need other sites fail** (OS packages, container images). Add the site to the list.
