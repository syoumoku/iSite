# Codex Task 00 — Bootstrap Foundation

## Goal

Turn this starter pack into a runnable MVP backend foundation.

## Read first

- `AGENTS.md`
- `.agents/skills/isite2-scan/SKILL.md`
- `docs/02_architecture.md`
- `config/*.yaml`
- `schemas/*.schema.json`

## Implement

1. Ensure the Python package installs with `pip install -e '.[dev]'`.
2. Ensure `pytest -q` passes.
3. Add config loading tests for `config/scenes.yaml` and `config/output_templates.yaml`.
4. Implement `/health`, `/rules/scenes`, `/rules/output-template`, `/scan-runs` smoke tests.
5. Add minimal repository interfaces but keep DB persistence optional for this task.
6. Do not implement real crawling yet.

## Acceptance

- `pytest -q` passes.
- `ruff check src tests` passes.
- FastAPI app starts.
- Scene rules and output template can be loaded.
- Demand formula tests remain exact.
