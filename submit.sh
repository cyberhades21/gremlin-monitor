#!/bin/sh
# submit: saves everything changed in the project, then sends the last of the activity record.
# The candidate types `submit` in the editor's terminal. The interviewer can also run it:
#   docker compose exec editor submit
set -eu

WORKSPACE=/home/coder/workspace
START_FILE="$HOME/.gremlin/start-commit"
KEY_FILE=/shared/wakapi.key
SUBMIT_URL="${SUBMIT_URL:-http://ingest:3000/submit}"

[ -s "$START_FILE" ] || { echo "Cannot submit: the project's starting commit was not recorded." >&2; exit 1; }
[ -s "$KEY_FILE" ] || { echo "Cannot submit: this session has no key." >&2; exit 1; }
start="$(cat "$START_FILE")"

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
cd "$WORKSPACE"

# A throwaway index, so that the candidate's own staging area and commits are left as they were.
# It holds the working folder as it is now: committed, uncommitted and new files. Files the project
# ignores (installed packages, caches) are left out.
GIT_INDEX_FILE="$tmp/index" git read-tree "$start"
GIT_INDEX_FILE="$tmp/index" git -c core.hooksPath=/dev/null add -A

changes() { GIT_INDEX_FILE="$tmp/index" git diff --cached --no-ext-diff --no-textconv --no-color "$@" "$start"; }
{
  echo "# session:            $(hostname)"
  echo "# submitted:          $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# project started at: $start"
  echo "# project head now:   $(git rev-parse HEAD) ($(git rev-parse --abbrev-ref HEAD))"
  echo "#"
  changes --stat | sed 's/^/# /'
  echo
  changes
} > "$tmp/submission.diff"

code="$(curl -s -o "$tmp/reply" -w '%{http_code}' --noproxy '*' \
  -H "Authorization: Bearer $(cat "$KEY_FILE")" -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary @"$tmp/submission.diff" "$SUBMIT_URL")" || code=000
if [ "$code" != "201" ]; then
  echo "Submitting failed (HTTP $code): $(cat "$tmp/reply" 2>/dev/null)" >&2
  [ "$code" != "413" ] || echo "The changes are larger than the 5 MB limit." >&2
  echo "Nothing was saved. Tell the interviewer before closing anything." >&2
  exit 1
fi
echo "Your changes are saved: $(cat "$tmp/reply")"

# The activity extension holds its newest events until another event comes more than 30 seconds
# after its last send. So wait, then open a note in the editor to be that event.
echo "Sending the last of the activity record. This takes about half a minute..."
sleep 31
note="$HOME/SUBMITTED.txt"
printf 'Submitted at %s. You can close this window.\n' "$(date -u +%H:%M:%SZ)" > "$note"
code-server --reuse-window "$note" >/dev/null 2>&1 || true
sleep 6
wakatime-cli --sync-offline-activity 0 >/dev/null 2>&1 || true
echo "Done. You can close the window."
