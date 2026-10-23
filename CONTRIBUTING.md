# Contributing to InboxShield AI

Thanks for helping improve local, privacy-first invoice auditing.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Make sure Ollama is running and the model named by `INBOXSHIELD_MODEL` is
installed before running the opt-in real-model test.

## Checks

```bash
make lint
make test
```

For real inference:

```bash
INBOXSHIELD_REAL_MODEL_TEST=1 .venv/bin/python -m pytest -m real_model -s
```

## Guidelines

- Keep deterministic tests offline. Use `FakeExtractor` or a stub client; only
  `tests/test_real_model.py` may call the model.
- Never fabricate extraction results in tests or demo data. Use synthetic
  documents and let the model read them.
- Preserve the rule that a finding is an investigation signal, not proof of
  fraud.
- Do not silently rewrite financial values during validation. Record an issue
  and route the invoice to review.
- Keep amounts as integer minor units for comparison. Do not add float-based
  financial equality.
- Add tests for new duplicate rules, parsers, and security boundaries.
- Redact secrets in logs and error messages.
- Do not commit `.env`, invoices, databases, logs, or generated demo files.
- Keep the dashboard bound to localhost unless you are deliberately adding a
  documented remote-deployment story.

## Pull requests

Describe the user-visible change, the tests run, and any model-dependent
behavior. If a change affects extraction accuracy, include the model name and a
synthetic example, never a real invoice.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability
that exposes invoice data or allows arbitrary file access.
