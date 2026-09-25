# M1 synthetic runtime security review

Date: 2026-09-19. Scoped release/advisory review, not an image/SBOM scan or M4/S07
completion. This review was finalized after the isolated test run and shutdown.
The architecture called for review before staging: that ordering was missed and
is recorded as a process deviation. No production deployment occurred. Perform
this review before future staging runs; recheck advisories at build/release time.

The pinned Keycloak 26.7.4 release includes six security fixes covering replay,
locale exhaustion, path matching, account lockout, impersonation and SAML handling.
The fixture uses that release, PostgreSQL, no direct grants, no service accounts,
no delegated impersonation role and loopback-only HTTPS. Release identity/fix IDs
are saved in m1-runtime-release-review.json. [Keycloak release](https://github.com/keycloak/keycloak/releases/tag/26.7.4).

PostgreSQL's current 17-series security fixes are included in 17.11. This was a new
synthetic cluster, so no old data/extensions/configuration needed an upgrade cleanup.
No replication or optional extensions were enabled. EDB's package checksum is a
received-byte pin, not an independently published checksum. [PostgreSQL security](https://www.postgresql.org/support/security/17/),
[17.11 release](https://www.postgresql.org/docs/17/release-17-11.html).

Temurin 21.0.12.1+1 is an official non-prerelease security update; the received JDK
hash matches the release metadata. [Official release](https://github.com/adoptium/temurin21-binaries/releases/tag/jdk-21.0.12.1%2B1),
[Adoptium security-update announcement](https://adoptium.net/de/news/2026/09/eclipse-temurin-8u504-110321-170201-210121-25041-26021-available).

For 18 exact Python package versions from the two install reports plus the four
HTTP/runtime pins, PyPI's version-specific vulnerability metadata returned no
non-withdrawn advisories. Sources and results are saved in
m1-python-vulnerability-review.json. This does not mean the complete transitive
environment is vulnerability-free. Authlib's HTTPX deprecation remains visible;
the HTTPX2 migration, full SBOM scan and disposition are M4/M7 work.

Disposition: the recorded synthetic M1 evidence is usable with these limits. The
runtime is stopped and its four ports are free. Runtime DB least privileges, host
secret ACLs, full transitive/JAR/native-library scanning and external penetration
review remain required before real identities/data. The synthetic cluster owner's
credentials and local certificate must never become deployment credentials.
