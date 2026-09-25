#!/bin/sh
# Run once on the server, in /opt/amfatec. Creates secrets, the internal cert and the
# realm file. Never overwrites an existing one and never prints a value.
set -eu
umask 077
cd "$(dirname "$0")"
APP_HOST=amfatec.31.97.186.120.sslip.io
AUTH_HOST=auth-amfatec.31.97.186.120.sslip.io
hex() { openssl rand -hex 32; }
b64() { openssl rand -base64 32; }

if [ ! -f .env ]; then
  cat > .env <<EOF
AUTH_HOST=$AUTH_HOST
AMFATEC_ORIGIN=https://$APP_HOST
AMFATEC_ENGINE_ORIGIN=https://engine:8443
AMFATEC_OIDC_ISSUER=https://$AUTH_HOST/realms/amfatec
AMFATEC_OIDC_CLIENT_ID=amfatec-bff
AMFATEC_OIDC_CLIENT_SECRET=$(hex)
AMFATEC_OIDC_AUDIENCE=amfatec-engine
AMFATEC_ENGINE_KEY=$(hex)
AMFATEC_DB_HOST=db
AMFATEC_DB_PORT=5432
AMFATEC_DB_USER=amfatec
AMFATEC_DB_PASSWORD=$(hex)
AMFATEC_DB_NAME=amfatec
AMFATEC_EVAL_DB_NAME=amfatec_eval
AMFATEC_TOKEN_KEY_ID=v1
AMFATEC_TOKEN_KEY=$(b64)
AMFATEC_DATA_KEY=$(b64)
AMFATEC_EVALUATION_KEY=$(b64)
AMFATEC_TEMPLATE_EXPIRES_AT=$(( $(date +%s) + 30 * 86400 ))
AMFATEC_ROUND_ID=amfatec-preview
AMFATEC_TLS_CERT=/certs/internal.crt
AMFATEC_TLS_KEY=/certs/internal.key
AMFATEC_BIND=0.0.0.0
KEYCLOAK_ADMIN_PASSWORD=$(hex)
EOF
  echo "created .env"
fi

if [ ! -f certs/internal.key ]; then
  mkdir -p certs
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 365 \
    -keyout certs/internal.key -out certs/internal.crt -subj "/CN=amfatec-internal" \
    -addext "subjectAltName=DNS:gateway,DNS:engine" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,digitalSignature,keyCertSign" 2>/dev/null
  chown -R 10001:10001 certs
  chmod 755 certs; chmod 644 certs/internal.crt; chmod 400 certs/internal.key
  echo "created internal cert"
fi

if [ ! -f realm.json ]; then
  SECRET=$(sed -n 's/^AMFATEC_OIDC_CLIENT_SECRET=//p' .env)
  cat > realm.json <<EOF
{"realm":"amfatec","displayName":"AmFatec","enabled":true,"sslRequired":"external",
 "registrationAllowed":false,"registrationEmailAsUsername":true,"resetPasswordAllowed":false,"bruteForceProtected":true,
 "accessTokenLifespan":300,"ssoSessionIdleTimeout":1800,
 "clients":[{"clientId":"amfatec-bff","enabled":true,"protocol":"openid-connect",
  "publicClient":false,"secret":"$SECRET","standardFlowEnabled":true,
  "directAccessGrantsEnabled":false,"serviceAccountsEnabled":false,
  "redirectUris":["https://$APP_HOST/auth/callback"],"webOrigins":["https://$APP_HOST"],
  "attributes":{"pkce.code.challenge.method":"S256",
   "backchannel.logout.url":"https://$APP_HOST/auth/backchannel-logout",
   "backchannel.logout.session.required":"true"},
  "protocolMappers":[
   {"name":"bff-introspection-audience","protocol":"openid-connect","protocolMapper":"oidc-audience-mapper",
    "config":{"included.client.audience":"amfatec-bff","id.token.claim":"false","access.token.claim":"true","introspection.token.claim":"true"}},
   {"name":"engine-audience","protocol":"openid-connect","protocolMapper":"oidc-audience-mapper",
    "config":{"included.custom.audience":"amfatec-engine","id.token.claim":"false","access.token.claim":"true","introspection.token.claim":"true"}}]}]}
EOF
  chmod 644 realm.json   # the sign-in server reads it as another uid; /opt/amfatec itself is 700
  echo "created realm.json"
fi
chmod 700 .
