.PHONY: test lint run validate-sample

test:
	.venv/bin/python -m pytest -q

lint:
	.venv/bin/python -m ruff check src tests

run:
	.venv/bin/python -m uvicorn isite2.api.main:app --reload

validate-sample:
	.venv/bin/python scripts/validate_packet.py examples/sample_site_packet.json schemas/site_packet.schema.json
