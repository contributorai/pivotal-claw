# Security policy

Please do not post credentials, private board data, or vulnerability details in
a public issue.

Use GitHub's private vulnerability reporting for security reports. Include the
affected version, reproduction steps, impact, and any suggested mitigation.

The application is local-first and has no built-in authentication. Do not expose
a laptop-mode instance directly to the public internet. The hosted demo uses
fictional data and disables laptop-only session and Terminal controls.

Never commit Postgres or ClickHouse credentials. Supply them through the hosting
platform's secret manager or a mode-0600 runtime secret file.
