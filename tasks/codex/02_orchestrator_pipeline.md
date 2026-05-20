# Codex Task 02 — Orchestrator Pipeline MVP

## Goal

Implement a deterministic pipeline that accepts fake candidates and produces complete SitePacket outputs.

## Implement

- Pipeline state model.
- Agent step interfaces.
- FakeDiscoveryAgent for deterministic tests.
- FakeEvidenceAgent with sample evidence fixtures.
- SceneModeling rule lookup from `config/scenes.yaml`.
- Demand calculation using scene parameter midpoint.
- QA gate execution and Review Queue creation.

## Acceptance

- Running a sample country scan creates multiple scene candidates.
- Every packet has entity, evidence, scene, build_status, demand, conclusion.
- Missing build evidence creates Review Queue but does not block output.
