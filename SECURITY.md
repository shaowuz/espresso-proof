# Security Policy

## Reporting a vulnerability

Report security issues privately through GitHub: open the repository's
**Security** tab and choose **Report a vulnerability**. That opens a private
advisory visible only to the maintainers.

Please do not report vulnerabilities in public issues, pull requests, or
discussions. A public report exposes the problem to everyone before there is a
fix.

Include what you have: affected version or commit, how to reproduce, and what
an attacker could do with it. A partial report is better than no report.

This is a small research project with no staffed on-call rotation. Expect a
first response in days, not hours.

## What this project does and does not claim

Espresso Proof runs locally and writes artifacts to a local runs directory. It
is a research workflow, not hardened infrastructure. Specifically:

- **It is not built to process secrets or high-sensitivity data.** Do not paste
  credentials, API keys, personal data, or confidential material into a run.
  Problem text and agent output are written to disk in plaintext and may be sent
  to whichever external agent you configure.
- **`espresso serve` is meant for `127.0.0.1`.** It has no authentication and no
  authorization. Do not expose it to a network you do not control.
- **External agents execute as you.** `ESPRESSO_EXTERNAL_AGENT_COMMAND` runs a
  command on your machine with your privileges. Only point it at an agent you
  trust.
- **Run artifacts are not sanitized for publication.** They may contain the full
  problem text and the agent's reasoning.

Reports that amount to "the local server has no auth" or "the configured agent
command runs code" describe documented design, not vulnerabilities. Reports that
show a run escaping its runs root, an artifact bypassing kernel validation, or a
trusted status being granted without the evidence the kernel requires are
security-relevant and we want to hear about them.

## Scope

In scope: this repository's `main` branch.
