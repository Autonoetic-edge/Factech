#!/bin/sh
# Enable/disable self-registration on the isolated AmFatec preview stack.
# Existing realms are not updated by --import-realm: update the running realm.
set -eu
umask 077
cd "$(dirname "$0")"
MODE=${1:-}
case "$MODE" in
  enable) ENABLED=true ;;
  disable) ENABLED=false ;;
  *) echo "usage: sh registration.sh enable|disable" >&2; exit 2 ;;
esac
docker compose exec -T auth sh -c 'KC_CLI_PASSWORD="$KC_BOOTSTRAP_ADMIN_PASSWORD" /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080 --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME"' >/dev/null
docker compose exec -T auth /opt/keycloak/bin/kcadm.sh update realms/amfatec \
  -s "registrationAllowed=$ENABLED" -s 'registrationEmailAsUsername=true' \
  -s 'duplicateEmailsAllowed=false' -s 'passwordPolicy="length(12) and notUsername(undefined)"'
if grep -q '^AMFATEC_SELF_REGISTRATION=' .env; then
  sed -i "s/^AMFATEC_SELF_REGISTRATION=.*/AMFATEC_SELF_REGISTRATION=$ENABLED/" .env
else
  printf '\nAMFATEC_SELF_REGISTRATION=%s\n' "$ENABLED" >> .env
fi
docker compose up -d --no-deps gateway
echo "Registration $MODE complete. Existing accounts and templates are retained."
