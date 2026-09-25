#!/bin/sh
# Registration form hints for the isolated AmFatec preview realm (UX plan Part 2, item 9).
# Makes the e-mail address the one identifier and drops first/last name from the form.
# Updates the RUNNING realm (startup import never touches an existing realm). Run from
# /opt/amfatec during a release window: sh realm-hints.sh apply | show | revert
# Nothing here is printed except kcadm's own output; no credential leaves the auth container.
set -eu
umask 077
cd "$(dirname "$0")"
MODE=${1:-}
case "$MODE" in apply|show|revert) ;; *) echo "usage: sh realm-hints.sh apply|show|revert" >&2; exit 2 ;; esac
KC=/opt/keycloak/bin/kcadm.sh
docker compose exec -T auth sh -c 'KC_CLI_PASSWORD="$KC_BOOTSTRAP_ADMIN_PASSWORD" '"$KC"' config credentials --server http://localhost:8080 --realm master --user "$KC_BOOTSTRAP_ADMIN_USERNAME"' >/dev/null
case "$MODE" in
  show)
    docker compose exec -T auth $KC get realms/amfatec --fields registrationAllowed,registrationEmailAsUsername,duplicateEmailsAllowed,resetPasswordAllowed,passwordPolicy
    docker compose exec -T auth $KC get realms/amfatec/users/profile
    ;;
  apply)
    # Keep what is there, so revert can put it back. Mode 600 via umask; no secrets inside.
    docker compose exec -T auth $KC get realms/amfatec/users/profile > user-profile.before.json
    docker compose exec -T auth $KC update realms/amfatec -s 'registrationEmailAsUsername=true' -s 'duplicateEmailsAllowed=false'
    docker compose exec -T auth $KC update realms/amfatec/users/profile -f - < user-profile.json
    echo "Applied: e-mail is the username; first/last name are optional on the form."
    ;;
  revert)
    [ -f user-profile.before.json ] || { echo "no user-profile.before.json to revert to" >&2; exit 1; }
    docker compose exec -T auth $KC update realms/amfatec -s 'registrationEmailAsUsername=false'
    docker compose exec -T auth $KC update realms/amfatec/users/profile -f - < user-profile.before.json
    echo "Reverted to the saved user profile; username registration is back."
    ;;
esac
