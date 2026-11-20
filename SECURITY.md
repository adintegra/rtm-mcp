# Security Policy

This server handles a Remember The Milk API key, shared secret and auth token, a calendar feed URL
(which can grant read access to your calendar), and can modify task due dates in RTM.

## Reporting a vulnerability

Please report vulnerabilities privately using GitHub's
[private vulnerability reporting](https://github.com/adintegra/rtm-mcp/security/advisories/new)
rather than opening a public issue. Include steps to reproduce and the potential impact.

You can expect an initial response within a few days. This is a small volunteer-maintained
project, so there are no guaranteed fix timelines.

## Supported versions

Only the latest release on `main` is supported.

## Handling credentials

- Pass credentials via environment variables or `RTM_TOKEN_FILE`; never commit them.
- Treat your `RTM_CALENDAR_ICS_URL` as a secret if it is a private feed URL.
- If a credential leaks, revoke it in RTM (and regenerate the calendar feed URL) immediately.
