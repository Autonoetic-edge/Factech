import hmac
import os

ENV_VAR = "ENGINE_API_KEY"

HEADER = "x-engine-key"

PROTECTED_PREFIX = "/v1/"


class MissingAPIKey(RuntimeError):
    pass


def configured_key() -> str | None:
    key = os.environ.get(ENV_VAR, "")
    return key if key.strip() else None


def load_api_key() -> str:
    key = configured_key()
    if key is None:
        raise MissingAPIKey(
            f"{ENV_VAR} is unset or blank. The engine authenticates every "
            f"/v1/* request with the {HEADER} header (contract section 2.3) "
            f"and refuses to start without a secret rather than defaulting "
            f"to open. Set {ENV_VAR} in .env (see .env.example) - generate "
            f"one with: "
            f'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return key


def is_valid(presented: str | None, expected: str | None = None) -> bool:
    if expected is None:
        expected = configured_key()
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def requires_key(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIX)
