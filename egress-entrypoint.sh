#!/bin/sh
# Starts the outbound proxy. It lets the editor reach the sites in egress-allowlist.txt plus
# the host the project is cloned from, and nothing else.
set -eu

ALLOWED=/etc/squid/allowed-domains.txt

# The list without comments and blank lines.
sed -E 's/[[:space:]]*#.*$//' /etc/squid/egress-allowlist.txt | grep -vE '^[[:space:]]*$' > "$ALLOWED" || true

# The host in REPO_URL, for example github.com from https://github.com/owner/repo.
host="$(printf '%s' "${REPO_URL:-}" | sed -nE 's#^[a-zA-Z][a-zA-Z0-9+.-]*://([^/@]*@)?([^/:]+).*#\2#p')"
if [ -n "$host" ]; then
  covered=0
  while read -r domain; do
    # Squid refuses a list where one entry sits inside another, so add the host only if it is new.
    case ".$host" in *"$domain") covered=1 ;; esac
  done < "$ALLOWED"
  [ "$covered" -eq 1 ] || printf '.%s\n' "$host" >> "$ALLOWED"
fi

echo "Outbound proxy allows: $(tr '\n' ' ' < "$ALLOWED")"
exec entrypoint.sh -f /etc/squid/squid.conf -NYC
