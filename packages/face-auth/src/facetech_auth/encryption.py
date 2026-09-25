"""Per-record AES-GCM envelopes with purpose-restricted, versioned wrapping keys.

The provider is injected; loading secrets never creates or repairs absent keys.
OS identities/ACL deployment and recovery custody are M7 obligations.
"""

import base64
import json
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .contracts import Unavailable


class KeyFailure(Unavailable):
    def __init__(self, code):
        super().__init__()
        self.code = code
        self.args = (code,)


class Keyring:
    def __init__(self, keys, active, *, purpose):
        if purpose not in {"template", "evaluation", "backup"}:
            raise ValueError("Explicit key purpose required")
        if (
            active not in keys
            or any(
                not isinstance(key, bytes) or len(key) != 32 for key in keys.values()
            )
            or any(
                not isinstance(version, str) or not version or len(version) > 80
                for version in keys
            )
        ):
            raise KeyFailure("KEY_UNAVAILABLE")
        self._keys, self.active, self.purpose = dict(keys), active, purpose

    @classmethod
    def from_file(cls, path, *, purpose, forbidden_roots):
        try:
            source = Path(path).resolve(strict=True)
        except OSError as exc:
            raise KeyFailure("KEY_UNAVAILABLE") from exc
        if any(source.is_relative_to(Path(root).resolve()) for root in forbidden_roots):
            raise ValueError("Keyring must be outside code, data and backup volumes")
        try:
            data = json.loads(source.read_text("utf-8"))
            if data["purpose"] != purpose:
                raise KeyFailure("KEY_PURPOSE_DENIED")
            return cls(
                {
                    v: base64.b64decode(k, validate=True)
                    for v, k in data["keys"].items()
                },
                data["active"],
                purpose=purpose,
            )
        except (ValueError, KeyError, OSError, TypeError, AttributeError) as exc:
            raise KeyFailure("KEY_UNAVAILABLE") from exc

    def key(self, version, purpose):
        if purpose != self.purpose:
            raise KeyFailure("KEY_PURPOSE_DENIED")
        if version not in self._keys:
            raise KeyFailure("KEY_UNAVAILABLE")
        return self._keys[version]

    def retire(self, version, *, references, backups_verified, backup_references):
        if (
            version == self.active
            or references
            or not backups_verified
            or backup_references
        ):
            raise KeyFailure("KEY_STILL_REFERENCED")
        self._keys.pop(version, None)


class EnvelopeCipher:
    def __init__(self, keyring):
        self.keyring = keyring

    @property
    def active_version(self):
        return self.keyring.active

    def ready(self, required_versions=()):
        for version in {self.active_version, *required_versions}:
            self.keyring.key(version, self.keyring.purpose)
        return True

    @staticmethod
    def context(tenant, kind, identity):
        return json.dumps(
            ["facetech-envelope", 1, tenant, kind, identity], separators=(",", ":")
        ).encode()

    @staticmethod
    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def decode(value):
        try:
            data = json.loads(value)
            if (
                set(data) != {"schema", "key_version", "wrapped", "ciphertext"}
                or type(data["schema"]) is not int
                or data["schema"] != 1
            ):
                raise ValueError()
            return data
        except (ValueError, TypeError, UnicodeError) as exc:
            raise KeyFailure("CIPHERTEXT_INVALID") from exc

    @staticmethod
    def encrypt(key, raw, aad):
        nonce = secrets.token_bytes(12)
        return base64.b64encode(nonce + AESGCM(key).encrypt(nonce, raw, aad)).decode()

    @staticmethod
    def decrypt(key, value, aad):
        try:
            raw = base64.b64decode(value, validate=True)
            return AESGCM(key).decrypt(raw[:12], raw[12:], aad)
        except (InvalidTag, ValueError, TypeError) as exc:
            raise KeyFailure("CIPHERTEXT_INVALID") from exc

    def seal(self, tenant, kind, identity, raw):
        version = self.active_version
        key = self.keyring.key(version, kind)
        context = self.context(tenant, kind, identity)
        dek = secrets.token_bytes(32)
        return self.encode(
            {
                "schema": 1,
                "key_version": version,
                "wrapped": self.encrypt(
                    key, dek, context + b"/wrap/" + version.encode()
                ),
                "ciphertext": self.encrypt(dek, raw, context + b"/data"),
            }
        )

    def open(self, tenant, kind, identity, value, *, expected_version=None):
        data = self.decode(value)
        version = data["key_version"]
        if not isinstance(version, str) or (
            expected_version is not None and version != expected_version
        ):
            raise KeyFailure("CIPHERTEXT_INVALID")
        context = self.context(tenant, kind, identity)
        dek = self.decrypt(
            self.keyring.key(version, kind),
            data["wrapped"],
            context + b"/wrap/" + version.encode(),
        )
        return self.decrypt(dek, data["ciphertext"], context + b"/data")

    def rewrap(self, tenant, kind, identity, value):
        # Authenticate both components before changing the wrapped data key.
        self.open(tenant, kind, identity, value)
        data = self.decode(value)
        context = self.context(tenant, kind, identity)
        old = data["key_version"]
        dek = self.decrypt(
            self.keyring.key(old, kind),
            data["wrapped"],
            context + b"/wrap/" + old.encode(),
        )
        new = self.active_version
        data.update(
            key_version=new,
            wrapped=self.encrypt(
                self.keyring.key(new, kind), dek, context + b"/wrap/" + new.encode()
            ),
        )
        return self.encode(data)


class DataCipher(EnvelopeCipher):
    """Compatibility constructor for synthetic M1/M2 fixtures; purpose required in services."""

    def __init__(self, key, *, purpose="template"):
        super().__init__(Keyring({"v1": key}, "v1", purpose=purpose))


def migrate_legacy(cipher, tenant, kind, identity, value, legacy_key):
    """Explicit offline migration only; never used by runtime reads."""
    try:
        aad = json.dumps(["v1", tenant, kind, identity]).encode()
        raw = AESGCM(legacy_key).decrypt(value[:12], value[12:], aad)
    except (InvalidTag, ValueError) as exc:
        raise KeyFailure("CIPHERTEXT_INVALID") from exc
    return cipher.seal(tenant, kind, identity, raw)
