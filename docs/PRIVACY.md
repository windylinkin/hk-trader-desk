# Privacy and publishing

Runtime settings, alert history, review notes and delivery history live in the operating system's application-data directory, outside this checkout by default. `HK_DESK_DATA_DIR` can override it. Do not choose a public or synced directory for private data.

Telegram token, receiving chat ID and Bark key use Windows DPAPI or macOS Keychain. Linux requires a usable Secret Service backend. The API reports whether credentials exist and never returns their values. The desktop notification switch is independent of mobile delivery. No project telemetry is implemented.

Futu receives market-data requests through your separately installed OpenD. Enabled phone channels transmit alert titles, symbols and message content to Telegram or Bark. Delivery records contain generic results rather than credential-bearing URLs. Third-party services apply their own policies.

The HTTP service binds to loopback. Host and Origin checks restrict browser access; it has no remote-user authentication and must not be exposed through a public proxy or port forwarding. A local administrator or malware can access a running user's data; encryption does not replace operating-system security.

Only the clean source repository is intended for publication. Never upload old runtime directories, OpenD binaries, databases, logs, credentials, backup scripts or screenshots of private settings. Before committing and before publishing run:

```sh
python scripts/privacy_check.py
git diff --cached
```

The scanner checks tracked files for common credential patterns, personal paths and forbidden runtime files. It cannot identify every possible secret or personal detail; review new content and commit metadata too. GitHub account names, public repository ownership and contributions remain publicly visible. Use a GitHub noreply address or a neutral project identity for commit email.

If a secret has been published, revoke/rotate it immediately. Removing it in a later commit does not remove earlier history. Never submit real credentials in issues or security reports.
