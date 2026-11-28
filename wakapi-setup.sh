#!/bin/sh
# Gives one session its own Wakapi user and API key, and saves the key where only that session's
# editor can read it. Safe to run again. Run it once per SESSION_ID.
#
# Why one user per session: Wakapi decides whose data a request is about from the API key. With a key
# of their own, a candidate can only ever send to, or read from, their own session's record.
#
# Wakapi cannot be given a user or key through its configuration, so this uses its signup and login pages.
set -eu

BASE="${WAKAPI_BASE:-http://wakapi:3000}"
SESSION_ID="${SESSION_ID:-editor}"

# The same rule as a container hostname, because SESSION_ID is also the editor's machine name.
case "$SESSION_ID" in
  '' | *[!a-z0-9-]* | -*)
    echo "SESSION_ID may contain only lowercase letters, digits and dashes, and cannot start with a dash." >&2
    exit 1
    ;;
esac

USER_NAME="s-$SESSION_ID"

# /shared/sessions/<id> is the only part of the volume that session's editor is given.
# /shared/operator holds the dashboard logins; no editor ever sees it.
SESSION_DIR="/shared/sessions/$SESSION_ID"
OPERATOR_DIR=/shared/operator
KEY_FILE="$SESSION_DIR/wakapi.key"
LOGIN_FILE="$OPERATOR_DIR/$SESSION_ID.login"
JAR=/tmp/cookies

mkdir -p "$SESSION_DIR" "$OPERATOR_DIR"
chmod 0755 /shared/sessions "$SESSION_DIR"
chmod 0700 "$OPERATOR_DIR"

echo "Waiting for Wakapi at $BASE"
i=0
until curl -fs -o /dev/null "$BASE/api/health"; do
  i=$((i + 1))
  [ "$i" -le 60 ] || { echo "Wakapi did not start within 60 seconds." >&2; exit 1; }
  sleep 1
done

# True when the saved key works AND belongs to this session's user, not another one.
key_works() {
  [ -s "$KEY_FILE" ] || return 1
  curl -fs -H "Authorization: Basic $(printf %s "$(cat "$KEY_FILE")" | base64 | tr -d '\n')" \
    "$BASE/api/compat/wakatime/v1/users/current" | grep -Eq "\"id\": ?\"$USER_NAME\""
}

if key_works; then
  echo "Session $SESSION_ID: the saved API key still works, nothing to do."
  exit 0
fi

if [ -s "$LOGIN_FILE" ]; then
  PASSWORD="$(cut -d: -f2- "$LOGIN_FILE")"
else
  PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
  # Saved before the signup, so that a failure part-way cannot leave a user whose password is lost.
  printf '%s:%s\n' "$USER_NAME" "$PASSWORD" > "$LOGIN_FILE"
  chmod 0600 "$LOGIN_FILE"
fi

# 302 means created, 409 means the user already exists (a later run).
code="$(curl -s -o /dev/null -w '%{http_code}' \
  --data-urlencode "username=$USER_NAME" --data-urlencode "email=$USER_NAME@example.com" \
  --data-urlencode "password=$PASSWORD" --data-urlencode "password_repeat=$PASSWORD" \
  --data-urlencode "location=UTC" "$BASE/signup")"
case "$code" in
  302|409) ;;
  *) echo "Wakapi signup failed with HTTP $code" >&2; exit 1 ;;
esac

code="$(curl -s -o /dev/null -w '%{http_code}' -c "$JAR" \
  --data-urlencode "username=$USER_NAME" --data-urlencode "password=$PASSWORD" "$BASE/login")"
if [ "$code" != "302" ]; then
  echo "Wakapi login failed with HTTP $code. If the user was made by an earlier setup whose saved login is gone," >&2
  echo "run 'docker compose down -v' to start Wakapi again from empty." >&2
  exit 1
fi

KEY="$(curl -s -b "$JAR" "$BASE/settings" | grep -o -E '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
[ -n "$KEY" ] || { echo "Could not find the API key on the Wakapi settings page." >&2; exit 1; }

printf '%s' "$KEY" > "$KEY_FILE"
chmod 0644 "$KEY_FILE"
key_works || { echo "The new API key was refused by Wakapi." >&2; exit 1; }
echo "Session $SESSION_ID is ready. Dashboard login: $USER_NAME / $PASSWORD"
