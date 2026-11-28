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

# The commit the project started from, so that `submit` can save everything changed since.
START_FILE="$HOME/.gremlin/start-commit"
if [ ! -s "$START_FILE" ]; then
  mkdir -p "$HOME/.gremlin"
  git -C "$WORKSPACE" rev-parse HEAD > "$START_FILE" || echo "No starting commit; submit will not work." >&2
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

# Maven does not read HTTP_PROXY, so give it the same proxy in its settings file.
if [ -n "${HTTPS_PROXY:-}" ]; then
  hostport="${HTTPS_PROXY#*://}"
  hostport="${hostport%/}"
  mkdir -p "$HOME/.m2"
  {
    echo '<settings>'
    echo '  <proxies>'
    for protocol in http https; do
      echo "    <proxy><id>egress-$protocol</id><active>true</active><protocol>$protocol</protocol>"
      echo "      <host>${hostport%:*}</host><port>${hostport##*:}</port>"
      echo '      <nonProxyHosts>localhost|127.0.0.1|ingest|db|mongo</nonProxyHosts></proxy>'
    done
    echo '  </proxies>'
    echo '</settings>'
  } > "$HOME/.m2/settings.xml"
fi

# The project's preparation (installing packages and so on), so that it is ready to run when the
# candidate arrives. Runs once, in the background, while the editor starts. Output: ~/setup.log.
# SETUP_COMMAND comes from the interviewer's catalogue.
if [ -n "${SETUP_COMMAND:-}" ] && [ ! -e "$HOME/.gremlin/setup" ]; then
  echo running > "$HOME/.gremlin/setup"
  (
    cd "$WORKSPACE"
    if sh -c "$SETUP_COMMAND" > "$HOME/setup.log" 2>&1; then echo done; else echo failed; fi > "$HOME/.gremlin/setup"
  ) &
fi

# code-server reads PASSWORD from the environment. Make one up when none is given.
if [ -z "${PASSWORD:-}" ]; then
  PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
  export PASSWORD
fi
echo "Editor password: $PASSWORD"

exec /usr/bin/entrypoint.sh --bind-addr 0.0.0.0:8080 --disable-telemetry --disable-update-check "$WORKSPACE"
