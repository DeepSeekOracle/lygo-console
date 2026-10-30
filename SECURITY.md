# Security policy — LYGO LLM Console 1.9.1

## Supported versions

| Version | Supported |
|---|---|
| 1.9.1 (Public Beta) | yes — this tag |
| anything older | no |

Beta means fixes land in the next beta, not as backports.

## Reporting a vulnerability

**Do not open a public issue for a working exploit.** Report privately to the steward:

- GitHub: open a [private security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities) on this repository, or
- the steward's published contact on <https://excavationpro.ca>.

Please include: affected version/tag, platform, the smallest reproduction you can manage, and what
an attacker gains. You will get an acknowledgement when it is read; there is no bounty and no
guaranteed fix date.

## What this software assumes

A single trusted user on a single trusted machine. The local engine and model are supplied by you and run as local processes on loopback ports 11441 (engine), 9641 (console), 8081/8082 (bridges).

## Secrets: the rules this repository enforces

The repository is built by a script that **fails the build** if a secret or a builder path is
present. It is not a substitute for care, but it means the committed tree has been scanned for:

- API key shapes (`AIza…`, `sk-…`, `nvapi-…`, `ghp_…`, `hf_…`, `xox…`, `AKIA…`),
- PEM/private-key blocks,
- `Authorization: Bearer …`,
- absolute developer paths.

If you fork this and add configuration, **never commit**:

- `config/api.json`, `config/local.json`, `config/admin.json`, `*.key`, `*.pem`, `.env`,
- browser profile/vault data, session files, download ledgers,
- model weights and engine binaries (they belong in a release, not in git history).

A secret that reaches a git history stays there. Rotate it; do not rewrite history and assume it is
gone.

## Default posture

- Services bind to **127.0.0.1**, not `0.0.0.0`. If you expose one on a network, you own the
  firewall, the TLS terminator and the authentication in front of it.
- No telemetry, no analytics, no auto-update.
- Model and API credentials are **yours**; they are read from local config at runtime and are never
  written into this repository.

## Known issues

See the "Known limits" section of [`README.md`](README.md). Those are documented behaviour, not
undisclosed vulnerabilities.
