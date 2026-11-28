#!/bin/sh
# Makes a random password salt for Wakapi once and keeps it. Safe to run again.
# Wakapi has no shell, so it cannot make its own; it reads this file when it starts
# (see compose.yaml). The salt has to stay the same between runs, or saved logins stop working.
set -eu

CONFIG=/config/wakapi.yml

if [ -s "$CONFIG" ]; then
  echo "Wakapi settings already exist, nothing to do."
  exit 0
fi

SALT="$(head -c 24 /dev/urandom | base64 | tr -d '/+=\n')"

# listen_ipv6 "-" turns IPv6 off, as the image's own settings do. A replacement settings file
# would otherwise make Wakapi listen twice on the same port and stop.
# signup_max_rate is raised from Wakapi's 5 an hour: each session gets its own Wakapi user, so
# starting more than five sessions an hour would otherwise be refused. Only the setup step can
# reach signup; the editor cannot (see ingest.Caddyfile).
printf 'server:\n  listen_ipv6: "-"\nsecurity:\n  password_salt: %s\n  signup_max_rate: 600/1h\n' "$SALT" > "$CONFIG.tmp"
chmod 0644 "$CONFIG.tmp"  # Wakapi runs as an unprivileged user and has to read it
mv "$CONFIG.tmp" "$CONFIG"
echo "Made a new random Wakapi salt."
