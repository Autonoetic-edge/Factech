#!/bin/sh
# Trust = public CAs (the sign-in server's Let's Encrypt cert) + our one internal cert.
set -eu
cat /etc/ssl/certs/ca-certificates.crt /certs/internal.crt > /tmp/ca-bundle.crt
export AMFATEC_CA_FILE=/tmp/ca-bundle.crt
case "${1:-}" in
  gateway|engine) exec python -m facetech_auth.serve "$1" ;;
  admin) shift; exec python -m facetech_auth.admin "$@" ;;
  *) echo "usage: gateway | engine | admin ..." >&2; exit 2 ;;
esac
