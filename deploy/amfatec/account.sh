#!/bin/sh
# Invite one person: ./account.sh <username>
# Creates the sign-in user with a one-time password (they must change it at first
# sign-in) and binds it to a new participant. The password goes to a root-only file,
# never to the screen.
set -eu
umask 077
cd "$(dirname "$0")"
NAME=${1:-}
echo "$NAME" | grep -Eq '^[a-z0-9._-]{3,32}$' || { echo "usage: $0 <username: a-z 0-9 . _ ->" >&2; exit 2; }
mkdir -p accounts
[ ! -e "accounts/$NAME.txt" ] || { echo "accounts/$NAME.txt already exists" >&2; exit 1; }

docker compose exec -T auth sh -c 'KC_CLI_PASSWORD="$KC_BOOTSTRAP_ADMIN_PASSWORD" /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME"' >/dev/null
PW=$(openssl rand -base64 18)
ID=$(printf '{"username":"%s","enabled":true,"email":"%s@example.invalid","emailVerified":true,"firstName":"%s","lastName":"Preview","credentials":[{"type":"password","value":"%s","temporary":true}]}' \
  "$NAME" "$NAME" "$NAME" "$PW" |
  docker compose exec -T auth /opt/keycloak/bin/kcadm.sh create users -r amfatec -f - -i)
ID=$(echo "$ID" | tr -d '\r\n')
printf '%s\n' "$PW" > "accounts/$NAME.txt"
docker compose run --rm --no-deps -T gateway admin account "$ID"
echo "one-time password is in $(pwd)/accounts/$NAME.txt (delete it after first sign-in)"
