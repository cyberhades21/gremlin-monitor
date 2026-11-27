#!/bin/sh
# Creates the Wakapi user once and saves its API key where the editor can read it. Safe to run again.
# Wakapi cannot be given a user or key through its configuration, so this uses its signup and login pages.
set -eu

BASE="${WAKAPI_BASE:-http://wakapi:3000}"
USER_NAME=interviewer
KEY_FILE=/shared/wakapi.key
LOGIN_FILE=/shared/wakapi-login.txt
JAR=/tmp/cookies

echo "Waiting for Wakapi at $BASE"
i=0
until curl -fs -o /dev/null "$BASE/api/health"; do
  i=$((i + 1))
  [ "$i" -le 60 ] || { echo "Wakapi did not start within 60 seconds." >&2; exit 1; }
  sleep 1
done

key_works() {
  [ -s "$KEY_FILE" ] || return 1
  curl -fs -o /dev/null -H "Authorization: Basic $(printf %s "$(cat "$KEY_FILE")" | base64 | tr -d '\n')" \
    "$BASE/api/compat/wakatime/v1/users/current"
}

if key_works; then
  echo "The saved API key still works, nothing to do."
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
echo "Wakapi is ready. Dashboard login: $USER_NAME / $PASSWORD"
