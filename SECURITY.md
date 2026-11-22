# Security Policy

## Scope & Protocol Status

OneMind is currently at protocol version `0.1-draft` (experimental, local M0 + M1 implementation). See [`docs/security.md`](docs/security.md) for the full threat model and enforced governance invariants.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for suspected security vulnerabilities (including authority bypasses, digest-binding flaws, path traversal / symlink escapes, or arbitrary code execution).

Instead, report vulnerabilities privately to the project maintainers using GitHub Private Vulnerability Reporting on this repository (or contact the maintainer listed in repository metadata) with:

- A clear description of the vulnerability and affected component/file.
- Minimal reproduction steps or a failing test fixture.
- Potential impact on governance, immutability, or local filesystem safety.

We will acknowledge receipt promptly, coordinate a fix and regression test, and credit reporters if desired.
