# Spike 2, hands-on: a browser editor for the candidate (code-server)

Status: run 2026-11-19, on one laptop, with a local model standing in for Claude (no Anthropic key). Only code-server was tried, so the comparison with OpenVSCode Server that spec 11 asks for is not done. The plan says stock code-server for Gate 1a, so nothing here blocks the pilot. The code is `spikes/sandbox/editor/` and `rig.py editor` (never promoted without a rewrite, spec 11). Browser checks were made with a headless Microsoft Edge only.

## Result in brief

1. **A candidate can do a whole session in a browser.** Log in, read `BRIEF.md`, edit code (auto-saved into the workspace folder on the host), open a terminal, run the agent in it, write `FINDINGS.yaml`, run `submit`. The archive lands on the host, and the existing `session collect` and `grade-session` commands accept it. The report reads as it should (G-001 "reported, not fixed").
2. **Stock code-server is not locked down, and three defaults had to change.** It ships GitHub Copilot as a built-in extension and shows its AI chat panel. It points the extension marketplace at open-vsx.org. And it opens a Welcome page with a Coder advertisement. All three are fixed in the image and the settings (see "Findings").
3. **With our configuration the browser made no call to any outside host** in a 100-second busy session and in the earlier runs. With plain defaults it made 26 to 45 calls to `open-vsx.org`. The telemetry address is still in the page's configuration, and I could not make the browser use it in either setup, so this does not prove telemetry is off (see "Telemetry").
4. **A page reload keeps the terminals and whatever runs in them**, including the agent. A candidate who refreshes by accident loses nothing.
5. **Memory is not a problem at this size.** About 0.9 GiB of the 2 GiB limit with code-server, three terminals, the agent's interface and its background service running.

## What was run

| Part | What |
|---|---|
| Editor | code-server 4.145.2 (VS Code 1.145.2), the `linux-amd64` release asset, SHA-256 `2f6db6c3e8b733dd6b3aeee83532e148b2bae2e392353ba85452d03572348962` (the digest GitHub shows for it), checked by the Dockerfile. Its LICENSE is MIT, Coder Technologies. The embedded VS Code folder has no licence file of its own, and `ThirdPartyNotices.txt` ships beside it. Not reviewed further |
| Image | `gassess-spike-ed:cs-4.145.2`: the OpenCode image plus code-server, PyYAML 6.0.3 (for `submit`), a copy of `src/workspace_tools`, the settings file and a start script. 1.7 GB. The release is 237 MB compressed |
| Sandbox | As in spike 1: read-only root, `--cap-drop ALL`, no new privileges, 2 GB, 2 CPUs, a network with no route out |
| Way in | A forwarder container on both networks that publishes one port on `127.0.0.1` only. A container on a no-route network cannot publish a port itself. The forwarder copies bytes and reads none, so the editor's password check and WebSockets pass through |
| Login | code-server's own password, random per session. It limits logins to two a minute and twelve an hour |
| Model | The local `qwen3:4b-instruct` behind the gateway, as in spike 1 |

Each launch runs eight checks inside the sandbox before it prints the address. They are E1 to E8 in the table below.

## Checks

| Check | Result |
|---|---|
| E1 The terminal runs as the unprivileged user | Pass |
| E2 A terminal has no route out and no outside DNS | Pass. Both the connection and the lookup are blocked. In the browser terminal, `curl https://example.com` says "Could not resolve host" |
| E3 The agent's lock file can be read and not changed | Pass. `touch` on it, and on `/usr/local/bin`, says "Read-only file system" |
| E4 `submit` and `opencode` are on the path | Pass |
| E5 The gateway refuses a call with no token | Pass (401) |
| E6 Installing an extension is refused | Pass. "No extension gallery service configured." The extensions view stays in the sidebar, and a search returns nothing |
| E7 The built-in Copilot extension is not in the image | Pass |
| E8 The editor's settings are in place | Pass |
| A wrong password is refused | Pass. The login page says "Incorrect password" and the failed attempt is in the editor's log |
| The gateway saw only the pinned model | Pass. 5 requests in one session, all for the session's model, all streamed, all with the user agent `opencode/latest/2.3.2/cli`. The two title requests carry no tools |
| An edit in the editor reaches the host | Pass. Auto save wrote it into the bind-mounted workspace within seconds |
| `submit` works from the terminal | Pass. It checked `FINDINGS.yaml`, wrote the archive and the `SUBMITTED` marker to the host |
| A reload keeps terminals alive | Pass. The shell's output came back and a process started before the reload was still running. Terminals and the agent's interface were also still running several minutes after the browser that opened them had gone. The editor's log shows a 3-hour reconnection grace time, and I did not wait that long |

## Findings that change the setup

1. **The built-in Copilot is in the release.** `lib/vscode/extensions/copilot` is 170 MB, and without settings the editor shows an AI chat panel ("Build with Agent") with its own model picker. That is a second assistant in a session whose point is one assistant that we log. The image deletes the extension (the image is read-only at run time) and the settings turn off the AI features and hide the empty side panel. I did not test which of the two does the work alone. The first probe, with the extension and no settings, showed the panel. The final setup shows nothing.
2. **The marketplace default is open-vsx.org.** An empty gallery (`EXTENSIONS_GALLERY='{}'`) switches it off. The editor then makes no marketplace calls, and `--install-extension` fails with a clear message. The settings also set `extensions.allowed` to nothing. The plan wants "extensions view and installation disabled". The view itself stays, but it is empty and nothing can be installed. Uploads are off too, so a candidate cannot bring in an extension file. They can still paste text.
3. **A Coder advertisement had to go.** With no settings the editor opens a Welcome page that carries it. The settings stop that page opening, so a candidate sees an empty editor with `BRIEF.md` in the file list. The editor's page configuration also reports its getting-started override as off (`isEnabledCoderGettingStarted = False`). I did not open the Help menu's version of that page.
4. **Closed routes.** The editor's path proxy, file downloads and file uploads are off, and Workspace Trust prompts are off. Each shows as off in the page configuration or the server log. The proxy is for reaching other services in the container, and downloads would let the repo be copied out with one click.
5. **`submit` needs PyYAML, which a problem's own environment lacks.** It is installed into a folder of its own so that it cannot change what the candidate's code imports. The spike image gets the tool by copying `src/workspace_tools`. A real image needs the same decision made once.
6. **OpenCode's interface works in the browser terminal.** The agent answered and set a session title through the gateway. It starts a background service (`opencode serve --service`) beside the interface. Its requests carried the same fingerprint as the interface's.
7. **An abandoned terminal keeps running.** If a candidate closes the tab, the agent and anything else in the terminal go on until the container is removed. That is harmless for the pilot and worth knowing when reading a transcript.

## Telemetry

- The page's configuration says `enableTelemetry: true` and `telemetryEndpoint: https://v1.telemetry.coder.com/track`, **even with `--disable-telemetry` and `telemetry.telemetryLevel: off`**. code-server's own compiled code defines the flag and I found nowhere in it that reads it (a text search of its `out` folder). I did not trace what the embedded VS Code does with it.
- I watched for browser calls to anything but the editor's own address. Every other hostname was made unresolvable, and each attempt was recorded. A plain-defaults run (100 seconds, files opened, search, settings, command palette, a terminal, then a page close, where telemetry libraries tend to send) made 26 calls to `open-vsx.org` and none to Coder. Our configuration made none. The recorder works (the 26 and 45 show it), so "never seen" is a real observation. It is still not proof, because a beacon could depend on something I did not do.
- Telemetry from the browser is the candidate's own machine calling out. The sandbox's closed network does not cover it. **The consent text should therefore not say that nothing leaves the editor.** It can say that the editor is open-source code-server with its telemetry switched off, and that the candidate's own browser and extensions are theirs.
- For Gate 1b a reverse proxy we control could add a `Content-Security-Policy` that allows connections to our own address only. That would make the question moot. I did not test whether VS Code's web page still works under such a policy.

## What was not tested

- OpenVSCode Server, so the comparison is not done.
- A real tunnel: the SSH tunnel the plan names for Gate 1a, HTTPS, and webviews, which need a secure context (so a plain-HTTP address on another machine would break markdown preview and similar). On the same machine, `127.0.0.1` counts as secure.
- Any browser except Edge, any phone or tablet, slow links, and more than one session at a time.
- The terminal recorder and the 10-minute snapshots. They are still not written.
- VS Code 1.145 has an "agent host" component of its own, which shows in the server log and in the files that mention telemetry. With the AI features switched off nothing of it showed in the interface. I did not look at what it does.
- The candidate's own browser extensions. A browser with its own assistant sidebar would sit beside the editor. The plan already says this cannot be prevented, and the report says so.
- Real Claude, cost, and how a candidate gets on with a strong model in this terminal.

## Open choices for the engineer

1. **Keep uploads and downloads off?** Off is safer for the pilot and costs a candidate nothing in this task. Say if a volunteer needs them.
2. **The consent wording** about the editor (see "Telemetry"). The co-founder drafts it, so the co-founder should have the sentence above.
3. **Try OpenVSCode Server too?** I would only do it if code-server causes trouble in the pilot.
4. **Sandbox memory.** 2 GB held with room to spare here. Give a real session 3 GB if a package's language server is heavy.

## Reproduce

```
uv run python spikes/sandbox/rig.py build-editor     # needs network once, for the 237 MB release
uv run python spikes/sandbox/rig.py editor           # prints an address and a password, then waits
```

Press any key in that window (or Ctrl+C) to end the session. Everything is saved to `pilot/spikes/sandbox/editor-<time>/` (outside git): the workspace, `submissions/`, `editor.log`, `self-check.json`, the gateway log, `requests.json` and the agent's own data.
