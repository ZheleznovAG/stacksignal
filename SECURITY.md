# Security

StackSignal is designed for one user on their own computer. Its launchers bind to
`127.0.0.1`; there is no user authentication. Host/origin validation and framing
protection reduce browser-based attacks, but are not a replacement for authentication.
Keep the service on loopback. Network hosting requires a separate security design.

## Private information

The SQLite database, profiles, votes, decisions, local folder paths, agent batches,
AI responses and backups are private runtime data in `data/`. Keys belong in `.env`.
Set `STACKSIGNAL_PRIVATE_DIR` to keep both outside the checkout; its private `.env`
and `data/` are loaded automatically. Project archives never include that separate folder.
Project descriptions and collected content are sent to an AI gateway or local agent
when you explicitly use that analysis workflow. External collectors contact their
respective APIs; Telegram sends a digest only when configured.

Do not attach a real database, `.env`, OAuth credentials, agent prompts or unreviewed
screenshots to issues. Use synthetic examples and remove account identifiers and paths.

## Reporting a vulnerability

Use the repository's **Security → Report a vulnerability** form when private
vulnerability reporting is enabled. If it is unavailable, open an issue requesting a
private reporting channel without including exploit details, secrets or personal data.
There is no guaranteed response time. Security fixes target the current main branch.
