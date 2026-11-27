#!/bin/sh
# Clone the repo named by REPO_URL (first start only), then run code-server on it.
set -eu

WORKSPACE=/home/coder/workspace
: "${REPO_URL:?REPO_URL is not set}"

if [ -z "$(ls -A "$WORKSPACE")" ]; then
  echo "Cloning $REPO_URL"
  git clone -- "$REPO_URL" "$WORKSPACE"
  if [ -n "${REPO_REF:-}" ]; then
    git -C "$WORKSPACE" checkout "$REPO_REF"
  fi
else
  echo "Workspace already has files, so nothing is cloned."
fi

# Commits need an author. These are placeholders the candidate can change.
git config --global user.name "${GIT_USER_NAME:-Candidate}"
git config --global user.email "${GIT_USER_EMAIL:-candidate@example.invalid}"

# Activity monitoring. The WakaTime extension reads ~/.wakatime.cfg. Without a server and key it is
# switched off, so that it does not pop up a request for an API key.
if [ -n "${WAKAPI_URL:-}" ] && [ -s /shared/wakapi.key ]; then
  printf '[settings]\napi_url = %s\napi_key = %s\n' "$WAKAPI_URL" "$(cat /shared/wakapi.key)" > "$HOME/.wakatime.cfg"
  echo "Activity monitoring is on, sending to $WAKAPI_URL"
else
  printf '[settings]\ndisabled = true\n' > "$HOME/.wakatime.cfg"
  echo "Activity monitoring is off (no WAKAPI_URL or no key in /shared/wakapi.key)."
fi

# code-server reads PASSWORD from the environment. Make one up when none is given.
if [ -z "${PASSWORD:-}" ]; then
  PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
  export PASSWORD
fi
echo "Editor password: $PASSWORD"

exec /usr/bin/entrypoint.sh --bind-addr 0.0.0.0:8080 --disable-telemetry --disable-update-check "$WORKSPACE"
