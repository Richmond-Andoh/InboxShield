# Security policy

InboxShield processes financial documents. Please report security issues
privately rather than opening a public issue with real invoice data.

## Supported versions

The latest released version on the default branch is supported with security
fixes.

## What to report

- Path traversal, symlink escapes, or preview access outside the configured
  document folders
- SQL injection or unsafe database handling
- CSV formula injection that survives export
- Secret leakage in logs or error messages
- Prompt-injection behavior that causes the model to alter financial fields
- Denial of service through crafted documents, PDFs, or model output
- Any upload or network call that sends document data to a non-local service

## How to report

Contact the maintainers privately through the repository's security advisory
feature, or the contact method listed by the project owner. Include:

- A concise description and impact
- Reproduction steps or a minimal synthetic document
- Affected version and configuration
- Whether the issue is already public

Do not attach real invoices, bank details, credentials, or personal data. Use
synthetic documents and redact paths and secrets.

## Security model and known limits

- Document processing is local by default. The Ollama endpoint is configurable;
  pointing it at a remote host sends documents there and is outside the intended
  privacy model.
- The model is treated as untrusted. Prompts tell it to ignore instructions in
  documents, and validation rejects malformed or inconsistent output, but local
  models can still be fooled. Human review remains required.
- Files are restricted to the watched directory; symlinks, hidden files,
  unsupported extensions, empty files, and oversized files are rejected.
- Dashboard previews are restricted to allowed document directories.
- SQL values are parameterized. Dynamic SQL identifiers come from fixed
  allowlists.
- CSV exports neutralize cells beginning with spreadsheet formula characters.
- The dashboard binds to localhost by default. Exposing it to a network requires
  an authenticated reverse proxy and a deliberate configuration change.
- No system can guarantee correct extraction from every scan.

## Secrets

Keep credentials in `.env`, never in source, screenshots, logs, or commits. The
project does not require cloud credentials. If you rotate a leaked secret, treat
it as compromised even if the commit is later removed.
