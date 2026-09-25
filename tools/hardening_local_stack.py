"""Explicit temporary PostgreSQL/Keycloak test stack, confined to E:\\Factech.

No system services, inherited .env, Docker, live URL or original checkout access.
Run prepare/start/stop only for this uniquely owned synthetic local cluster.
Secrets remain in .hardening-runtime/state and must never enter evidence output.
"""

import argparse
import base64
import datetime
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".hardening-runtime"
STATE = RUNTIME / "state"
sys.path[:0] = [str(ROOT / ".hardening-deps"), str(ROOT / "packages/face-auth/src")]


def prepare():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    STATE.mkdir(parents=True, exist_ok=True)
    secret_file = STATE / "synthetic-secrets.json"
    if secret_file.exists():
        print("Synthetic local stack already prepared; secrets retained.")
        return
    password = secrets.token_urlsafe(32)
    values = {
        "marker": "facetech-m1-synthetic-only",
        "pg_password": password,
        "client_secret": secrets.token_urlsafe(32),
        "engine_key": secrets.token_urlsafe(32),
        "token_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "data_key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "users": {
            name: {"id": str(uuid.uuid4()), "password": secrets.token_urlsafe(24)}
            for name in ("alice", "bob", "carol", "reviewer", "admin", "operator")
        },
    }
    secret_file.write_text(json.dumps(values), encoding="utf-8")
    now = datetime.datetime.now(datetime.UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Facetech synthetic local test CA")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    (STATE / "tls.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (STATE / "tls.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pg = RUNTIME / "bin/pgsql/bin"
    pwfile = STATE / "pg-init-password.txt"
    pwfile.write_text(password, encoding="utf-8")
    try:
        subprocess.run(
            [
                str(pg / "initdb.exe"),
                "-D",
                str(STATE / "postgres"),
                "-U",
                "facetech_test",
                "--pwfile=" + str(pwfile),
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C",
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    finally:
        pwfile.unlink(missing_ok=True)
    (STATE / "owned-cluster.json").write_text(
        json.dumps(
            {
                "marker": values["marker"],
                "data": str((STATE / "postgres").resolve()),
                "port": 15432,
            }
        ),
        encoding="utf-8",
    )
    print("Prepared owned synthetic cluster and short-lived loopback TLS certificate.")


def check_free(port):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))


def start_postgres():
    import psycopg

    values = json.loads((STATE / "synthetic-secrets.json").read_text("utf-8"))
    check_free(15432)
    subprocess.run(
        [
            str(RUNTIME / "bin/pgsql/bin/pg_ctl.exe"),
            "-D",
            str(STATE / "postgres"),
            "-l",
            str(STATE / "postgres.log"),
            "-o",
            "-h 127.0.0.1 -p 15432 -c max_connections=40 -c shared_buffers=64MB -c log_statement=none",
            "-w",
            "start",
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    with psycopg.connect(
        host="127.0.0.1",
        port=15432,
        user="facetech_test",
        password=values["pg_password"],
        dbname="postgres",
        autocommit=True,
    ) as connection:
        for dbname in ("facetech_m1", "facetech_idp", "facetech_eval"):
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (dbname,)
            ).fetchone():
                connection.execute(
                    psycopg.sql.SQL("CREATE DATABASE {}").format(
                        psycopg.sql.Identifier(dbname)
                    )
                )
    print("Owned PostgreSQL test process listening only on 127.0.0.1:15432.")


def start_keycloak():
    check_free(18443)
    values = json.loads((STATE / "synthetic-secrets.json").read_text("utf-8"))
    home = RUNTIME / "bin/keycloak-26.7.4"
    imports = home / "data/import"
    imports.mkdir(parents=True, exist_ok=True)
    realm = {
        "realm": "facetech-hardening",
        "enabled": True,
        "sslRequired": "all",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
        "clients": [
            {
                "clientId": "facetech-bff",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": values["client_secret"],
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": ["https://127.0.0.1:18444/auth/callback"],
                "webOrigins": ["https://127.0.0.1:18444"],
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                    "backchannel.logout.url": "https://127.0.0.1:18444/auth/backchannel-logout",
                    "backchannel.logout.session.required": "true",
                },
                "protocolMappers": [
                    {
                        "name": "bff-introspection-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.client.audience": "facetech-bff",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    },
                    {
                        "name": "engine-audience",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.custom.audience": "facetech-engine",
                            "id.token.claim": "false",
                            "access.token.claim": "true",
                            "introspection.token.claim": "true",
                        },
                    },
                ],
            }
        ],
        "users": [
            {
                "id": v["id"],
                "username": k,
                "enabled": True,
                "firstName": "Synthetic",
                "lastName": k,
                "email": k + "@example.invalid",
                "emailVerified": True,
                "credentials": [
                    {"type": "password", "value": v["password"], "temporary": False}
                ],
            }
            for k, v in values["users"].items()
        ],
    }
    (imports / "facetech-hardening-realm.json").write_text(
        json.dumps(realm), encoding="utf-8"
    )
    environment = {
        **os.environ,
        "KC_DB": "postgres",
        "KC_DB_URL": "jdbc:postgresql://127.0.0.1:15432/"
        + values.get("provider_database", "facetech_idp"),
        "KC_DB_USERNAME": "facetech_test",
        "KC_DB_PASSWORD": values["pg_password"],
        "KC_TRUSTSTORE_PATHS": str(STATE / "tls.crt"),
    }
    command = [
        str(RUNTIME / "bin/jdk-21.0.12.1+1/bin/java.exe"),
        "-Xms128m",
        "-Xmx512m",
        "-XX:ActiveProcessorCount=2",
        "-Djava.util.concurrent.ForkJoinPool.common.threadFactory=io.quarkus.bootstrap.forkjoin.QuarkusForkJoinWorkerThreadFactory",
        "-Djava.io.tmpdir=" + str(RUNTIME / "tmp"),
        "-Duser.home=" + str(STATE),
        "-Dkc.home.dir=" + str(home),
        "-Djboss.server.config.dir=" + str(home / "conf"),
        "-cp",
        str(home / "lib/quarkus-run.jar"),
        "io.quarkus.bootstrap.runner.QuarkusEntryPoint",
        "start",
        "--import-realm",
        "--hostname=https://127.0.0.1:18443",
        "--http-host=127.0.0.1",
        "--https-port=18443",
        "--http-enabled=false",
        "--https-certificate-file=" + str(STATE / "tls.crt"),
        "--https-certificate-key-file=" + str(STATE / "tls.key"),
        "--cache=local",
        "--db-pool-min-size=1",
        "--db-pool-max-size=5",
        "--log-level=warn",
    ]
    with (STATE / "keycloak.log").open("ab") as log:
        subprocess.run(
            command[: command.index("start")] + ["build", "--db=postgres"],
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        command.insert(command.index("start") + 1, "--optimized")
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    (STATE / "keycloak-process.json").write_text(
        json.dumps({"pid": process.pid, "exe": command[0]}), encoding="utf-8"
    )
    print("Started owned Keycloak test process; startup is asynchronous.")


def stop():
    import psutil

    marker = json.loads((STATE / "owned-cluster.json").read_text("utf-8"))
    if marker != {
        "marker": "facetech-m1-synthetic-only",
        "data": str((STATE / "postgres").resolve()),
        "port": 15432,
    }:
        raise ValueError("Owned-cluster marker mismatch")
    for name in (
        "gateway-process.json",
        "engine-process.json",
        "keycloak-process.json",
    ):
        path = STATE / name
        if path.exists():
            saved = json.loads(path.read_text("utf-8"))
            try:
                process = psutil.Process(saved["pid"])
                if Path(process.exe()).resolve() != Path(saved["exe"]).resolve():
                    raise ValueError("Process identity mismatch; refusing termination")
                arguments = process.cmdline()
                owned_argument = (
                    "-Dkc.home.dir=" + str(RUNTIME / "bin/keycloak-26.7.4")
                    if name == "keycloak-process.json"
                    else str(ROOT / "tools/hardening_synthetic_service.py")
                )
                if owned_argument not in arguments:
                    raise ValueError(
                        "Owned process command mismatch; refusing termination"
                    )
                process.terminate()
                process.wait(timeout=15)
            except psutil.NoSuchProcess:
                pass
    subprocess.run(
        [
            str(RUNTIME / "bin/pgsql/bin/pg_ctl.exe"),
            "-D",
            str(STATE / "postgres"),
            "-m",
            "fast",
            "-w",
            "stop",
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    print("Stopped only the owned synthetic local test processes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "postgres", "keycloak", "stop"))
    args = parser.parse_args()
    {
        "prepare": prepare,
        "postgres": start_postgres,
        "keycloak": start_keycloak,
        "stop": stop,
    }[args.action]()
