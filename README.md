# InboxShield AI

Local, privacy-first invoice auditing with Gemma, Ollama, SQLite, and Streamlit.

InboxShield watches a folder for invoice images and PDFs, extracts the accounting
fields with a **local vision model**, flags explainable duplicate risks, and puts
uncertain documents in a human review queue. Nothing is sent to a cloud AI
provider, and the audit ledger stays in a local SQLite database.

This is an audit assistant, not a fraud detector and not an accounting system. A
duplicate flag is an investigation signal, never proof of wrongdoing.

## Features

- Watches `incoming_invoices/` for PNG, JPEG, and PDF files
- Renders up to three PDF pages and extracts fields with a local Ollama vision model
- Stores money as validated decimal text plus integer minor units; never uses float equality
- Detects exact-file duplicates by SHA-256 before running AI again
- Explains potential duplicates using vendor, invoice number, amount, currency, and dates
- Keeps the original extracted values; validation issues never silently rewrite financial data
- Provides a review queue with reviewer notes and audit events
- Uses a transactional SQLite ledger with WAL mode, foreign keys, and indexes
- Runs a heartbeat, retry/reconciliation loop, and a localhost-only dashboard
- Exports filtered invoices and findings as CSV with formula-injection protection
- Includes a synthetic demo dataset, unit tests, a real-model test, and an environment check

## How it works

```text
incoming_invoices/
        |
        v
file safety checks -> stable-file wait -> SHA-256
        |                                  |
        |                                  +--> exact duplicate? stop, attach finding
        v
local Gemma vision model through Ollama
        |
        v
schema validation and normalization
        |
        v
duplicate / risk rules
        |
        v
SQLite audit ledger  --->  Streamlit dashboard
```

The daemon and dashboard are separate processes. The daemon does ingestion and
inference; the dashboard only reads the database and writes review decisions.

## Requirements

- Linux, macOS, or Windows with Python 3.11+
- [Ollama](https://ollama.com/) running locally
- An installed Ollama vision model. This project was built and tested with
  `gemma4:e2b`; other local vision models can be used by changing
  `INBOXSHIELD_MODEL`.

A CPU-only machine works but is slow. In testing, `gemma4:e2b` took roughly
60-120 seconds per page. A GPU or a smaller vision model will be faster.

## Quick start

```bash
git clone <your-repository-url>
cd InboxShield

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt

cp .env.example .env
# Edit .env if your Ollama URL or model tag differs.

ollama serve
# In another terminal:
ollama pull gemma4:e2b

python scripts/check_environment.py
python daemon.py
```

In a second terminal:

```bash
source .venv/bin/activate
streamlit run app.py
```

Open <http://127.0.0.1:8501>. The dashboard is configured to bind to localhost
only.

## Demo

The demo generator creates real synthetic images and a two-page PDF. It does not
insert fake extraction results into the database; the local model must read the
documents.

```bash
python scripts/generate_demo_invoices.py --clean
python daemon.py --once
streamlit run app.py
```

Expected result shape:

| File | What should happen |
| --- | --- |
| `DEMO-01_normal_accra_supplies.png` | Verified, invoice #1 |
| `DEMO-02_normal_kumasi_printworks.png` | Verified, invoice #2 |
| `DEMO-03_exact_copy_of_01.png` | No new invoice; exact-file duplicate finding on invoice #1 |
| `DEMO-04_potential_duplicate_accra.png` | Review: same vendor/currency/total, different invoice number |
| `DEMO-05_number_conflict_accra.png` | Review: reused invoice number with a different total |
| `DEMO-06_missing_fields.png` | Review: missing invoice number and currency |
| `DEMO-07_multipage_invoice.pdf` | Two-page extraction; totals merged from page 2 |

Model output can vary. A field the model cannot read is kept as missing and sent
to review instead of being guessed.

To remove only demo data:

```bash
python scripts/reset_demo_data.py          # dry run
python scripts/reset_demo_data.py --yes    # archive demo documents and remove the DB
```

## Configuration

All settings are environment variables; `.env` is loaded automatically. See
`.env.example` for the complete list.

Important values:

| Variable | Default | Purpose |
| --- | --- | --- |
| `INBOXSHIELD_MODEL` | `gemma4:e2b` | Ollama vision model name |
| `INBOXSHIELD_OLLAMA_URL` | `http://localhost:11434` | Local Ollama endpoint |
| `INBOXSHIELD_INCOMING_DIR` | `incoming_invoices` | Watched folder |
| `INBOXSHIELD_DB_PATH` | `data/inboxshield.db` | SQLite audit ledger |
| `INBOXSHIELD_MAX_FILE_SIZE_MB` | `20` | Per-document size limit |
| `INBOXSHIELD_MAX_PDF_PAGES` | `3` | Maximum PDF pages inspected |
| `INBOXSHIELD_MAX_RETRIES` | `3` | Maximum processing attempts |
| `INBOXSHIELD_DUPLICATE_WINDOW_DAYS` | `30` | Date window for amount-based review |
| `INBOXSHIELD_VENDOR_SIMILARITY` | `0.86` | Similar-vendor threshold |
| `INBOXSHIELD_AMOUNT_TOLERANCE_MINOR` | `0` | Amount tolerance in minor units |

The model must be a real vision-capable model. `scripts/check_environment.py`
checks that Ollama is reachable, the configured model exists, and it reports
vision capability.

### Optional IMAP

IMAP ingestion is **not implemented**. The related environment variables are
reserved so a future adapter can be added without changing the configuration
surface. Do not expect mail fetching to work in this version.

## Review and audit semantics

- `VERIFIED_LOGGED`: no review finding was produced.
- `FLAGGED_REVIEW`: one or more fields or relationships need a human decision.
- `FLAGGED_DUPLICATE`: a strong vendor + invoice number + amount + currency match.
- `FAILED`: extraction or persistence failed; see the job error.

Exact-file duplicates do not create a second financial record. The finding is
attached to the existing invoice and enters the review queue.

Reviewing a finding records the decision, reviewer note, timestamp, and an audit
event. It does not repay, approve, or delete an invoice.

## Data and privacy

- Documents and the SQLite database stay on the local machine.
- The Ollama request goes to the configured local endpoint.
- Logs redact common secret patterns; do not put secrets in filenames.
- Database rows, generated documents, logs, and `.env` are excluded by
  `.gitignore`.
- Generated demo documents are fictional and marked
  `SYNTHETIC DEMO DOCUMENT - NOT A REAL INVOICE`.

No system can guarantee perfect extraction from every scan. Check the source
document before making a payment decision.

## Development

```bash
make install-dev
make lint
make test
make check
```

Run the opt-in real-model test:

```bash
INBOXSHIELD_REAL_MODEL_TEST=1 .venv/bin/python -m pytest -m real_model -s
```

Useful commands:

```bash
python scripts/check_environment.py   # infrastructure and model checks
python daemon.py --once              # process the current queue once
python daemon.py --allow-degraded    # start without a healthy model (not for extraction)
streamlit run app.py                 # dashboard
```

The test suite uses fake extraction clients for deterministic tests. The real
model test is the only test that performs inference.

## Project layout

```text
app.py                         Streamlit entrypoint
daemon.py                      ingestion daemon and worker lifecycle
inboxshield/
  ai/                          Ollama client, prompts, extraction, validation
  audit/                       duplicate rules, risk logic, persistence
  ingestion/                   file safety, processor, watcher, reconciliation
  services/                    query, CSV export, health checks
  dashboard/                   dashboard pages and components
  config.py                    validated settings
  database.py                  SQLite schema and data access
  models.py                    domain enums and result objects
  schemas.py                   model schema, normalization, merging
scripts/                       demo generator and maintenance tools
tests/                         deterministic tests and opt-in real-model test
```

## Troubleshooting

**`Ollama is unavailable`** - Start `ollama serve` and confirm
`curl http://localhost:11434/api/tags`.

**`model not found`** - Run `ollama pull <model>` using the exact model name in
`INBOXSHIELD_MODEL`.

**Extraction is very slow** - CPU inference is expected to be slow. Use a GPU,
reduce the image dimension, or choose a smaller vision model. PDF pages are
processed one at a time.

**A document is stuck in review** - Open the dashboard review queue, read the
explanation, and record a reviewer note. The original values remain available
for comparison.

**The daemon heartbeat looks active but no files move** - Check
`logs/inboxshield.log`, then use the dashboard health page. A stale heartbeat is
reported rather than treated as a running daemon.

## Limitations

- Invoice layouts and scan quality vary; extraction can miss or misread fields.
- The model may return placeholder text; common placeholders are normalized to
  missing values, but human review is still required.
- There is no bank integration, payment execution, OCR fallback, or cloud model
  fallback.
- Multi-page handling is bounded by `INBOXSHIELD_MAX_PDF_PAGES`; later pages are
  not inspected.
- Currency totals are grouped by currency and are never added across currencies.
- Duplicate rules are heuristics. They are intentionally explainable rather than
  opaque.

## License

Apache License 2.0. See [LICENSE](LICENSE).
