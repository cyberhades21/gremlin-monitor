"""The HTML of the interviewer console (designed in Stitch, project "Gremlin Interviewer Console").

Everything that comes from a session (file names, branches, submitted code) is the candidate's
text, so every value goes through e() before it reaches the page.
"""

import datetime
import html
import re

import sessions

STATUS = {"starting": "Starting", "ready": "Ready", "stopped": "Not running", "ending": "Ending",
          "ended": "Ended", "failed": "Failed", "deleting": "Deleting"}
MAX_EVENTS = 300
MAX_DIFF_LINES = 600


def e(value):
    return html.escape("" if value is None else str(value), quote=True)


def clock(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


def when(iso):
    if not iso:
        return ""
    return datetime.datetime.fromisoformat(iso).astimezone().strftime("%d %b, %H:%M")


def pill(status):
    return f'<span class="pill pill-{e(status)}">{e(STATUS.get(status, status))}</span>'


def csrf_field(csrf):
    return f'<input type="hidden" name="csrf" value="{e(csrf)}">'


def layout(title, body, csrf="", active="", refresh=0):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    nav = ""
    if csrf:
        links = ""
        for key, href, text in (("sessions", "/", "Sessions"), ("new", "/new", "New session")):
            marked = ' class="active"' if key == active else ""
            links += f'<a href="{href}"{marked}>{text}</a>'
        nav = (f'<nav>{links}</nav><form class="signout" method="post" action="/logout">{csrf_field(csrf)}'
               '<button class="plain">Sign out</button></form>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{meta}'
            f'<title>{e(title)} · Gremlin</title><link rel="stylesheet" href="/static/style.css"></head>'
            f'<body><header class="top"><span class="brand">Gremlin</span>{nav}</header>'
            f'<main>{body}</main><script src="/static/app.js" defer></script></body></html>')


def login(error=""):
    message = f'<p class="alert">{e(error)}</p>' if error else ""
    body = ('<section class="card narrow"><h1>Interviewer sign-in</h1>'
            f'{message}<form method="post" action="/login">'
            '<label for="password">Password</label>'
            '<input id="password" name="password" type="password" autocomplete="current-password" required autofocus>'
            '<button class="primary wide">Sign in</button></form>'
            '<p class="hint">The password was printed in the window where the app first started.</p></section>')
    return layout("Sign in", body)


def session_list(records, last_activity, csrf):
    rows = ""
    for record in records:
        rows += (f'<tr><td class="strong">{e(record["label"])}</td>'
                 f'<td><span class="chip">{e(record["project_name"])}</span></td>'
                 f'<td>{pill(record["shown_status"])}</td>'
                 f'<td class="muted">{e(when(record["created"]))}</td>'
                 f'<td class="muted">{e(last_activity.get(record["id"], ""))}</td>'
                 f'<td class="right"><a href="/sessions/{e(record["id"])}">Open →</a></td></tr>')
    if records:
        table = ('<div class="card flush"><table><thead><tr><th>Candidate label</th><th>Project</th>'
                 '<th>Status</th><th>Created</th><th>Last activity</th><th></th></tr></thead>'
                 f'<tbody>{rows}</tbody></table></div>')
    else:
        table = ('<div class="card empty">No sessions yet. Create one to get a link for a candidate.</div>')
    body = ('<div class="page-head"><h1>Sessions</h1>'
            '<a class="button primary" href="/new">+ New session</a></div>'
            f'{table}<p class="hint">Links work only on this computer for now.</p>')
    refresh = 5 if any(r["shown_status"] in ("starting", "ending", "deleting") for r in records) else 0
    return layout("Sessions", body, csrf, "sessions", refresh)


def new_session(projects, csrf, error=""):
    groups = ""
    first = True
    for key, title in sessions.DIFFICULTIES:
        chosen = [p for p in projects if p["difficulty"] == key]
        cards = ""
        for project in chosen:
            tags = "".join(f'<span class="tag">{e(t)}</span>' for t in project.get("stack", []))
            cards += (f'<label class="project"><input type="radio" name="project" value="{e(project["id"])}"'
                      f' data-name="{e(project["name"])}" data-stack="{e(", ".join(project.get("stack", [])))}"'
                      f'{" checked" if first else ""} required>'
                      f'<span class="project-body"><span class="project-name">{e(project["name"])}</span>'
                      f'<span class="muted">{e(project.get("description", ""))}</span>'
                      f'<span class="tags">{tags}</span>'
                      f'<span class="chip mono">{e(project["repo"].removeprefix("https://"))}'
                      f' @ {e(project.get("ref", "")[:7] or "latest")}</span></span></label>')
            first = False
        cards = cards or '<div class="none">No projects yet</div>'
        groups += (f'<div class="group"><div class="group-head"><h3>{e(title)}</h3>'
                   f'<span class="muted">{len(chosen)} project{"" if len(chosen) == 1 else "s"}</span></div>'
                   f'{cards}</div>')
    message = f'<p class="alert">{e(error)}</p>' if error else ""
    default = next((p for key, _ in sessions.DIFFICULTIES for p in projects if p["difficulty"] == key), None)
    summary_name = e(default["name"]) if default else ""
    summary_stack = e(", ".join(default.get("stack", []))) if default else ""
    body = ('<h1>New session</h1><p class="muted lead">Pick a project. Each session gets its own editor, '
            f'link and password.</p>{message}'
            f'<form class="split" method="post" action="/sessions">{csrf_field(csrf)}'
            f'<div>{groups}</div>'
            '<aside class="card sticky"><h2>Session details</h2>'
            '<label for="label">Candidate label</label>'
            '<input id="label" name="label" maxlength="80" required placeholder="Backend candidate A">'
            '<p class="hint">A name you will recognise. Do not enter personal details you do not need.</p>'
            '<hr><p class="eyebrow">Selected project</p>'
            f'<p class="strong" id="summary-name">{summary_name}</p>'
            f'<p class="muted" id="summary-stack">{summary_stack}</p><hr>'
            '<button class="primary wide">Create session and link</button>'
            '<p class="hint center">Starting and preparing takes a few minutes.</p></aside></form>')
    return layout("New session", body, csrf, "new")


# ---------------------------------------------------------------- one session


def activity(events):
    files = [ev for ev in events if not ev["entity"].endswith("/SUBMITTED.txt")]
    if not files:
        return None
    return {
        "files": len({ev["entity"] for ev in files}),
        "saves": sum(1 for ev in files if ev.get("is_write")),
        "net": sum(ev.get("human_line_changes") or 0 for ev in files),
        "span": int(files[-1]["time"] - files[0]["time"]),
        "events": files,
    }


def parse_diff(text):
    header, files, current, in_hunk = [], [], None, False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*) b/(.*)$", line)
            current = {"name": match.group(2) if match else line[11:], "add": 0, "del": 0, "lines": [], "kind": ""}
            files.append(current)
            in_hunk = False
        elif current is None:
            if line.startswith("#"):
                header.append(line[1:].strip())
        elif line.startswith("@@"):
            in_hunk = True
            current["lines"].append(("hunk", line))
        elif not in_hunk:
            if line.startswith("new file"):
                current["kind"] = "new"
            elif line.startswith("deleted file"):
                current["kind"] = "deleted"
            elif line.startswith(("rename ", "Binary files")):
                current["lines"].append(("hunk", line))
        elif line.startswith("+"):
            current["add"] += 1
            current["lines"].append(("add", line))
        elif line.startswith("-"):
            current["del"] += 1
            current["lines"].append(("del", line))
        else:
            current["lines"].append(("ctx", line))
    return header, files


def diff_view(record, submissions, chosen):
    if not submissions:
        return ('<div class="card empty">Nothing submitted yet. The candidate types <code>submit</code> in the '
                'editor, and ending the session takes a final copy.</div>')
    names = sorted(submissions, reverse=True)
    name = chosen if chosen in submissions else names[0]
    _, files = parse_diff(submissions[name])
    picker = ""
    if len(names) > 1:
        links = " ".join(
            f'<a class="chip{" selected" if n == name else ""}" href="/sessions/{e(record["id"])}?submission={e(n)}">'
            f'{e(stamp(n))}</a>' for n in names)
        picker = f'<div class="picker">{links}</div>'
    head = (f'<div class="row-between"><span class="muted">Saved {e(stamp(name))} · {len(files)} file'
            f'{"" if len(files) == 1 else "s"} changed</span>'
            f'<a href="/sessions/{e(record["id"])}/submissions/{e(name)}">Download</a></div>')
    blocks = ""
    for index, changed in enumerate(files):
        shown = changed["lines"][:MAX_DIFF_LINES]
        code = "".join(f'<span class="{cls}">{e(line) or " "}</span>' for cls, line in shown)
        more = ""
        if len(changed["lines"]) > len(shown):
            more = (f'<p class="hint">{len(changed["lines"]) - len(shown)} more lines. Download to read them.</p>')
        kind = f'<span class="tag">{e(changed["kind"])}</span>' if changed["kind"] else ""
        blocks += (f'<details class="file"{" open" if index < 3 else ""}><summary><span class="mono">'
                   f'{e(changed["name"])}</span> {kind}<span class="counts"><b class="plus">+{changed["add"]}</b> '
                   f'<b class="minus">−{changed["del"]}</b></span></summary>'
                   f'<pre class="diff">{code}</pre>{more}</details>')
    if not files:
        blocks = '<div class="card empty">This submission has no changes.</div>'
    return picker + head + blocks


def stamp(name):
    moment = datetime.datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone().strftime("%d %b, %H:%M:%S")


def duration(seconds):
    minutes = seconds // 60
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m {seconds % 60:02d}s"


def session_detail(record, data, csrf, chosen=""):
    status = record["shown_status"]
    sid = e(record["id"])
    actions = f'<a class="button" href="/sessions/{sid}">Refresh</a>'
    if status == "ready":
        actions += (f'<form method="post" action="/sessions/{sid}/end">{csrf_field(csrf)}'
                    '<button class="button outline">End session</button></form>')
    if status in ("stopped", "failed"):
        actions += (f'<form method="post" action="/sessions/{sid}/start">{csrf_field(csrf)}'
                    '<button class="button outline">Start again</button></form>')
    if status not in ("starting", "ending", "deleting"):
        actions += f'<a class="button" href="/sessions/{sid}/delete">Delete</a>'

    head = (f'<div class="page-head"><div><h1>{e(record["label"])} {pill(status)}</h1>'
            f'<p class="muted">{e(record["project_name"])} · <span class="mono">{e(record["ref"][:7] or "latest")}'
            f'</span> · created {e(when(record["created"]))}</p></div><div class="actions">{actions}</div></div>')

    note = ""
    if status == "ready" and record.get("setup") == "failed":
        note = ('<div class="card alert-box">Preparing the project failed. The editor works; look at setup.log in '
                'its home folder (cat ~/setup.log) before sending the link.</div>')
    if status == "starting":
        step = "Preparing the project (installing its packages)" if record.get("key") else "Starting the editor"
        note = (f'<div class="card info">{step}. This takes a few minutes; the page refreshes by itself.</div>')
    elif status == "ending":
        note = ('<div class="card info">Taking a final copy of the work and the record, then stopping. '
                'About a minute; the page refreshes by itself.</div>')
    elif status == "deleting":
        note = '<div class="card info">Deleting.</div>'
    elif status == "stopped":
        note = ('<div class="card info">The editor is not running (Docker or this computer may have restarted). '
                'Start again to bring it back with the candidate\'s work.</div>')
    if record.get("error"):
        note += f'<pre class="card alert-box">{e(record["error"])}</pre>'
    if data.get("problem"):
        note += f'<div class="card alert-box">{e(data["problem"])}</div>'

    consent = "Consent not accepted yet."
    if data["consent"]:
        latest = data["consent"][-1]
        consent = f'Consent accepted {clock(latest["time"])} (text version {e(latest["version"])})'

    share = ""
    if status == "ready":
        link = f'http://127.0.0.1:{record["port"]}/'
        share = ('<section class="card"><h2>Send this to the candidate</h2><div class="share">'
                 f'<div><p class="eyebrow">Link</p><div class="copy"><a class="mono" href="{e(link)}" target="_blank" '
                 f'rel="noopener noreferrer">{e(link)}</a><button type="button" data-copy="{e(link)}">Copy</button></div></div>'
                 '<div><p class="eyebrow">Editor password</p><div class="copy">'
                 f'<span class="mono">{e(record["password"])}</span>'
                 f'<button type="button" data-copy="{e(record["password"])}">Copy</button></div></div></div>'
                 f'<p class="muted">{consent} · The link works only on this computer for now.</p></section>')
    elif status in ("ended", "stopped"):
        ended = f' Ended {e(when(record.get("ended_at", "")))}.' if status == "ended" else ""
        share = f'<p class="muted">{consent}.{ended}</p>'

    summary = activity(data["events"])
    if summary:
        tiles = (('Files touched', summary["files"], ""), ('Saves', summary["saves"], ""),
                 ('Net lines changed', f'{summary["net"]:+d}', "includes pasted text"),
                 ('Time span', duration(summary["span"]), "first to last event"))
    else:
        tiles = (('Files touched', "–", ""), ('Saves', "–", ""), ('Net lines changed', "–", ""), ('Time span', "–", ""))
    stats = '<div class="stats">' + "".join(
        f'<div class="card stat"><p class="eyebrow">{e(t)}</p><p class="big">{e(v)}</p><p class="hint">{e(h)}</p></div>'
        for t, v, h in tiles) + "</div>"

    if summary:
        events = summary["events"]
        rows = ""
        for event in events[-MAX_EVENTS:]:
            path = event["entity"].replace("/home/coder/workspace/", "")
            saved = bool(event.get("is_write"))
            change = f'{event.get("human_line_changes") or 0:+d}' if saved else ""
            rows += (f'<tr><td class="muted mono">{clock(event["time"])}</td>'
                     f'<td class="mono path" title="{e(path)}">{e(path)}</td>'
                     f'<td>{"Saved" if saved else "Opened"}</td><td class="muted">{e(event.get("category", ""))}</td>'
                     f'<td class="right mono">{change}</td></tr>')
        older = (f'<p class="hint">Showing the latest {MAX_EVENTS} of {len(events)} events.</p>'
                 if len(events) > MAX_EVENTS else "")
        table = ('<div class="card flush scroll"><table class="compact"><thead><tr><th>Time</th><th>File</th><th></th>'
                 f'<th>Category</th><th class="right">Lines</th></tr></thead><tbody>{rows}</tbody></table></div>{older}')
    else:
        table = '<div class="card empty">No activity recorded yet. Events can take a few minutes to arrive.</div>'

    limits = ('<div class="card limits"><strong>What this record cannot show:</strong> terminal commands, changes made '
              'by scripts or agents, or whether text was typed or pasted. Events can arrive a few minutes late. '
              'Treat it as a rough timeline; the submitted changes are what shows the work.</div>')

    body = head + note + share
    if status in ("ready", "ended", "stopped"):
        body += (stats + '<div class="columns"><section><h2>Activity</h2>' + table + '</section>'
                 '<section><h2>Submitted changes</h2>' + diff_view(record, data["submissions"], chosen)
                 + '</section></div>' + limits)
    refresh = 4 if status in ("starting", "ending", "deleting") else 0
    return layout(record["label"], body, csrf, "sessions", refresh)


def confirm_delete(record, csrf):
    sid = e(record["id"])
    body = (f'<section class="card narrow"><h1>Delete this session?</h1><p><strong>{e(record["label"])}</strong>, '
            f'{e(record["project_name"])}</p><p>This removes the editor, the candidate\'s work, the activity record, '
            'the consent entry and the submitted changes from this computer. It cannot be undone.</p>'
            f'<form class="actions" method="post" action="/sessions/{sid}/delete">{csrf_field(csrf)}'
            f'<button class="button primary">Delete</button><a class="button" href="/sessions/{sid}">Cancel</a>'
            '</form></section>')
    return layout("Delete session", body, csrf, "sessions")


def message(title, text, csrf=""):
    return layout(title, f'<section class="card narrow"><h1>{e(title)}</h1><p>{e(text)}</p>'
                         '<p><a href="/">Back to sessions</a></p></section>', csrf)
