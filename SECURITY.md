# Security policy

## Local-first defaults

- API keys, cookies, and connector credentials stay on the host running the backend.
- The browser client never receives provider API keys.
- Logs redact authorization headers, cookies, and token-like values.
- The LAN listener is disabled by default; remote access should use a pairing token and HTTPS or a private tailnet.

## Reporting a vulnerability

Please do not open a public issue with credentials, cookies, or a working exploit. Use a private GitHub security advisory when this repository is published, or contact the maintainers through the repository profile.

