from __future__ import annotations

from isite2.orchestrator.pipeline import run_scan_pipeline
from isite2.rag import RagService
from isite2.repositories.sqlalchemy import SQLAlchemyScanRunRepository


def test_rag_index_is_idempotent_and_query_returns_citations(tmp_path) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'rag.db'}",
        storage_mode="sqlite",
    )
    result = run_scan_pipeline(
        {
            "level": "country",
            "countries": ["Algeria"],
            "full_scan": True,
            "scene_types": ["airport_terminal"],
            "output_formats": ["geojson"],
        },
        repository,
    )
    service = RagService(repository.engine)

    first = service.index(scan_run_id=result.scan_run.run_id)
    second = service.index(scan_run_id=result.scan_run.run_id)
    answer = service.query(
        question="Why is this airport a priority?",
        scan_run_id=result.scan_run.run_id,
        top_k=4,
    )

    assert first.indexed_documents >= 1
    assert first.indexed_chunks >= 1
    assert second.indexed_documents == 0
    assert second.skipped_documents >= 1
    assert answer.citations
    assert all(citation.source_url for citation in answer.citations)
    assert all(citation.source_tier for citation in answer.citations)
    assert all(citation.property_id or citation.raw_evidence_id for citation in answer.citations)
    assert answer.priority_recommendations
    assert answer.review_actions


def test_rag_query_without_indexed_scope_refuses_strong_conclusion(tmp_path) -> None:
    repository = SQLAlchemyScanRunRepository.from_url(
        f"sqlite+pysqlite:///{tmp_path / 'empty-rag.db'}",
        storage_mode="sqlite",
    )
    service = RagService(repository.engine)

    answer = service.query(question="Which buildings should we build first?", country="Nowhere")

    assert answer.citations == []
    assert "cannot make an evidence-backed recommendation" in answer.answer
