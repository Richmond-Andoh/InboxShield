PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
STREAMLIT ?= .venv/bin/streamlit

.PHONY: help install install-dev init daemon dashboard demo test lint format check reset-demo

help:
	@echo "InboxShield AI targets:"
	@echo "  install       Create .venv and install runtime dependencies"
	@echo "  install-dev   Install runtime + test/lint dependencies"
	@echo "  init          Initialize the SQLite database"
	@echo "  daemon        Run the background ingestion daemon"
	@echo "  dashboard     Run the Streamlit dashboard (localhost:8501)"
	@echo "  demo          Generate synthetic invoices into incoming_invoices"
	@echo "  test          Run the test suite"
	@echo "  lint          Run Ruff checks"
	@echo "  format        Format with Ruff"
	@echo "  check         Run lint, tests, and environment check"
	@echo "  reset-demo    Dry-run demo reset (use RESET_ARGS=--yes to apply)"

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

install-dev:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

init:
	$(PYTHON) -m inboxshield.database --init

daemon:
	$(PYTHON) daemon.py

dashboard:
	$(STREAMLIT) run app.py

demo:
	$(PYTHON) scripts/generate_demo_invoices.py --clean

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

check: lint test
	$(PYTHON) scripts/check_environment.py

reset-demo:
	$(PYTHON) scripts/reset_demo_data.py $(RESET_ARGS)
