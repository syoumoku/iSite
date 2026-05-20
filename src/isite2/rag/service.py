from __future__ import annotations

import hashlib
import math
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from isite2.db.models import (
    Base,
    BuildStatusDB,
    ConclusionDB,
    DemandEstimateDB,
    EvidenceItemDB,
    PropertyDB,
    RagChunkDB,
    RagDocumentDB,
    RawEvidenceItemDB,
    ReviewQueueDB,
    ScanCandidateDB,
    SceneModelResultDB,
    SourceCacheDB,
)
from isite2.db.session import create_session_factory


class EmbeddingProvider(Protocol):
    model_name: str
    dim: int

    def embed(self, text: str) -> list[float]:
        ...


class DeterministicEmbeddingProvider:
    """Small deterministic embedding provider for tests and local fallback."""

    model_name = "deterministic-hash-v1"

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or int(os.getenv("RAG_EMBEDDING_DIM", "64"))

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = re.findall(r"[\w']+", text.casefold())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


@dataclass(frozen=True)
class RagIndexResult:
    indexed_documents: int
    indexed_chunks: int
    skipped_documents: int


@dataclass(frozen=True)
class RagCitation:
    chunk_id: str
    source_url: str | None
    source_name: str | None
    source_tier: str | None
    source_date: str | None
    fetched_at: str | None
    raw_evidence_id: str | None
    property_id: str | None
    field_group: str | None
    excerpt: str


@dataclass(frozen=True)
class RagPriorityRecommendation:
    property_id: str | None
    property_name: str
    country: str | None
    scene_type: str | None
    priority_band: str
    evidence_status: str | None
    action_class: str | None
    recommended_solution: str | None
    rationale: str
    review_next_actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RagQueryResult:
    answer: str
    citations: list[RagCitation]
    priority_recommendations: list[RagPriorityRecommendation]
    review_actions: list[str]


@dataclass(frozen=True)
class _DocumentPayload:
    source_kind: str
    source_id: str
    title: str
    content_text: str
    source_url: str | None = None
    source_name: str | None = None
    source_tier: str | None = None
    source_date: str | None = None
    fetched_at: datetime | None = None
    raw_evidence_id: str | None = None
    property_id: str | None = None
    scan_run_id: str | None = None
    country: str | None = None
    city: str | None = None
    scene_type: str | None = None
    field_group: str | None = None
    indicator_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content_text.encode("utf-8")).hexdigest()


class RagService:
    def __init__(
        self,
        engine: Engine,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.engine = engine
        self.session_factory: sessionmaker[Session] = create_session_factory(engine)
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider()
        Base.metadata.create_all(engine)

    def index(
        self,
        *,
        scan_run_id: UUID | None = None,
        property_id: UUID | None = None,
        country: str | None = None,
    ) -> RagIndexResult:
        with self.session_factory.begin() as session:
            documents = self._documents(session, scan_run_id, property_id, country)
            indexed_documents = 0
            indexed_chunks = 0
            skipped_documents = 0
            for payload in documents:
                existing = session.scalar(
                    select(RagDocumentDB).where(
                        RagDocumentDB.source_kind == payload.source_kind,
                        RagDocumentDB.source_id == payload.source_id,
                    )
                )
                if existing is not None and existing.content_hash == payload.content_hash:
                    skipped_documents += 1
                    continue
                if existing is not None:
                    session.execute(
                        delete(RagChunkDB).where(RagChunkDB.document_id == existing.id)
                    )
                    document = existing
                    document.content_hash = payload.content_hash
                    document.updated_at = datetime.now(UTC)
                    _apply_document_payload(document, payload)
                else:
                    document = _document_row(payload)
                    session.add(document)
                    session.flush()
                chunks = _chunk_text(payload.content_text)
                for index, text in enumerate(chunks):
                    embedding = self.embedding_provider.embed(text)
                    session.add(
                        _chunk_row(
                            document,
                            payload,
                            index,
                            text,
                            embedding,
                            self.embedding_provider.model_name,
                        )
                    )
                    indexed_chunks += 1
                indexed_documents += 1
            return RagIndexResult(
                indexed_documents=indexed_documents,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped_documents,
            )

    def query(
        self,
        *,
        question: str,
        scan_run_id: UUID | None = None,
        property_id: UUID | None = None,
        country: str | None = None,
        scene_type: str | None = None,
        top_k: int = 5,
    ) -> RagQueryResult:
        question = question.strip()
        if not question:
            return RagQueryResult(
                answer="Ask iSite2 needs a question before it can retrieve evidence.",
                citations=[],
                priority_recommendations=[],
                review_actions=[],
            )
        query_embedding = self.embedding_provider.embed(question)
        with self.session_factory() as session:
            chunks = self._candidate_chunks(
                session,
                scan_run_id=scan_run_id,
                property_id=property_id,
                country=country,
                scene_type=scene_type,
            )
            ranked = sorted(
                (
                    (_chunk_score(chunk, query_embedding, question), chunk)
                    for chunk in chunks
                    if chunk.embedding
                ),
                key=lambda item: item[0],
                reverse=True,
            )[: max(1, top_k)]
            if not ranked:
                return RagQueryResult(
                    answer=(
                        "I cannot make an evidence-backed recommendation yet because no "
                        "indexed evidence matches this scope. Run /rag/index after discovery "
                        "or add field-level evidence first."
                    ),
                    citations=[],
                    priority_recommendations=[],
                    review_actions=[],
                )
            selected = [chunk for _, chunk in ranked]
            citations = [_citation(chunk) for chunk in selected]
            recommendations = self._priority_recommendations(session, selected)
            review_actions = self._review_actions(session, selected)
            return RagQueryResult(
                answer=_answer(question, selected, recommendations),
                citations=citations,
                priority_recommendations=recommendations,
                review_actions=review_actions,
            )

    def _documents(
        self,
        session: Session,
        scan_run_id: UUID | None,
        property_id: UUID | None,
        country: str | None,
    ) -> list[_DocumentPayload]:
        documents: list[_DocumentPayload] = []
        documents.extend(_raw_evidence_documents(session, property_id, country))
        documents.extend(_source_cache_documents(session, country))
        documents.extend(_property_documents(session, scan_run_id, property_id, country))
        return [document for document in documents if document.content_text.strip()]

    def _candidate_chunks(
        self,
        session: Session,
        *,
        scan_run_id: UUID | None,
        property_id: UUID | None,
        country: str | None,
        scene_type: str | None,
    ) -> list[RagChunkDB]:
        statement = select(RagChunkDB)
        if scan_run_id is not None:
            statement = statement.where(RagChunkDB.scan_run_id == str(scan_run_id))
        if property_id is not None:
            statement = statement.where(RagChunkDB.property_id == str(property_id))
        if country:
            statement = statement.where(RagChunkDB.country == country)
        if scene_type:
            statement = statement.where(RagChunkDB.scene_type == scene_type)
        return list(session.scalars(statement).all())

    def _priority_recommendations(
        self,
        session: Session,
        chunks: list[RagChunkDB],
    ) -> list[RagPriorityRecommendation]:
        property_ids = [chunk.property_id for chunk in chunks if chunk.property_id]
        seen: set[str] = set()
        recommendations: list[RagPriorityRecommendation] = []
        for pid in property_ids:
            if pid in seen:
                continue
            seen.add(pid)
            property_row = session.get(PropertyDB, pid)
            if property_row is None:
                continue
            conclusion = _latest_for_property(session, ConclusionDB, pid)
            review_rows = _latest_reviews(session, pid)
            recommendations.append(
                RagPriorityRecommendation(
                    property_id=pid,
                    property_name=property_row.canonical_name,
                    country=property_row.country,
                    scene_type=property_row.scene_type,
                    priority_band=_priority_band(conclusion),
                    evidence_status=conclusion.evidence_status if conclusion else None,
                    action_class=conclusion.action_class if conclusion else None,
                    recommended_solution=(
                        conclusion.recommended_solution if conclusion else None
                    ),
                    rationale=(
                        conclusion.reason_to_recommend
                        if conclusion
                        else "Indexed evidence exists, but no conclusion record is available."
                    ),
                    review_next_actions=[row.next_action for row in review_rows],
                )
            )
        return recommendations

    def _review_actions(self, session: Session, chunks: list[RagChunkDB]) -> list[str]:
        actions: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            if chunk.property_id:
                for row in _latest_reviews(session, chunk.property_id):
                    if row.next_action not in seen:
                        actions.append(row.next_action)
                        seen.add(row.next_action)
            elif chunk.raw_evidence_id and "missing" not in seen:
                actions.append("Curate raw evidence and map it to a verified property candidate.")
                seen.add("missing")
        return actions


def _raw_evidence_documents(
    session: Session,
    property_id: UUID | None,
    country: str | None,
) -> list[_DocumentPayload]:
    statement = select(RawEvidenceItemDB)
    if country:
        statement = statement.where(RawEvidenceItemDB.country == country)
    rows = session.scalars(statement).all()
    documents: list[_DocumentPayload] = []
    for row in rows:
        payload = row.payload or {}
        documents.append(
            _DocumentPayload(
                source_kind="raw_evidence",
                source_id=row.id,
                title=row.property_name or row.source_name,
                content_text="\n".join(
                    part
                    for part in [
                        row.property_name or "",
                        row.field_group or "",
                        row.indicator_name or "",
                        row.field_value or "",
                        str(payload.get("content_text") or ""),
                    ]
                    if part
                ),
                source_url=row.source_url,
                source_name=row.source_name,
                source_tier=row.source_tier,
                source_date=row.source_date,
                raw_evidence_id=row.id,
                country=row.country,
                city=row.city,
                scene_type=row.scene_type,
                field_group=row.field_group,
                indicator_name=row.indicator_name,
                metadata={"status": row.status, "source_type": row.source_type},
            )
        )
    return documents if property_id is None else []


def _source_cache_documents(session: Session, country: str | None) -> list[_DocumentPayload]:
    rows = session.scalars(select(SourceCacheDB)).all()
    evidence_by_url: dict[str, EvidenceItemDB] = {
        row.source_url: row for row in session.scalars(select(EvidenceItemDB)).all()
    }
    property_by_id: dict[str, PropertyDB] = {
        row.id: row for row in session.scalars(select(PropertyDB)).all()
    }
    raw_by_url: dict[str, RawEvidenceItemDB] = {
        row.source_url: row for row in session.scalars(select(RawEvidenceItemDB)).all()
    }
    if country:
        country_urls = {
            row.source_url
            for row in session.scalars(
                select(RawEvidenceItemDB).where(RawEvidenceItemDB.country == country)
            ).all()
        }
        country_urls.update(
            row.source_url
            for row in session.scalars(
                select(EvidenceItemDB)
                .join(PropertyDB, EvidenceItemDB.property_id == PropertyDB.id)
                .where(PropertyDB.country == country)
            ).all()
        )
        rows = [row for row in rows if row.source_url in country_urls]
    documents: list[_DocumentPayload] = []
    for row in rows:
        evidence = evidence_by_url.get(row.source_url)
        raw = raw_by_url.get(row.source_url)
        property_row = property_by_id.get(str(evidence.property_id)) if evidence else None
        documents.append(
            _DocumentPayload(
                source_kind="source_cache",
                source_id=row.source_url,
                title=row.source_name,
                content_text=row.content_text,
                source_url=row.source_url,
                source_name=row.source_name,
                source_tier=row.source_tier,
                source_date=row.source_date,
                fetched_at=row.fetched_at,
                raw_evidence_id=raw.id if raw else None,
                property_id=str(evidence.property_id) if evidence else None,
                scan_run_id=str(evidence.scan_run_id) if evidence else None,
                country=property_row.country if property_row else (raw.country if raw else None),
                city=property_row.city if property_row else (raw.city if raw else None),
                scene_type=(
                    property_row.scene_type if property_row else (raw.scene_type if raw else None)
                ),
                field_group=(
                    evidence.field_group if evidence else (raw.field_group if raw else None)
                ),
                indicator_name=(
                    evidence.indicator_name if evidence else (raw.indicator_name if raw else None)
                ),
                metadata={"robots_allowed": row.robots_allowed},
            )
        )
    return documents


def _property_documents(
    session: Session,
    scan_run_id: UUID | None,
    property_id: UUID | None,
    country: str | None,
) -> list[_DocumentPayload]:
    candidates = select(ScanCandidateDB).where(ScanCandidateDB.property_id.is_not(None))
    if scan_run_id is not None:
        candidates = candidates.where(ScanCandidateDB.scan_run_id == str(scan_run_id))
    if property_id is not None:
        candidates = candidates.where(ScanCandidateDB.property_id == str(property_id))
    rows = session.scalars(candidates.order_by(ScanCandidateDB.created_at.desc())).all()
    documents: list[_DocumentPayload] = []
    seen: set[tuple[str, str]] = set()
    for candidate in rows:
        pid = str(candidate.property_id)
        run_id = str(candidate.scan_run_id)
        key = (pid, run_id)
        if key in seen:
            continue
        seen.add(key)
        property_row = session.get(PropertyDB, pid)
        if property_row is None or (country and property_row.country != country):
            continue
        documents.append(_property_document(session, property_row, run_id))
    return documents


def _property_document(
    session: Session,
    property_row: PropertyDB,
    scan_run_id: str,
) -> _DocumentPayload:
    pid = str(property_row.id)
    evidence = session.scalars(
        select(EvidenceItemDB).where(
            EvidenceItemDB.property_id == pid,
            EvidenceItemDB.scan_run_id == scan_run_id,
        )
    ).all()
    scene = session.scalar(
        select(SceneModelResultDB).where(
            SceneModelResultDB.property_id == pid,
            SceneModelResultDB.scan_run_id == scan_run_id,
        )
    )
    build = session.scalar(
        select(BuildStatusDB).where(
            BuildStatusDB.property_id == pid,
            BuildStatusDB.scan_run_id == scan_run_id,
        )
    )
    demand = session.scalar(
        select(DemandEstimateDB).where(
            DemandEstimateDB.property_id == pid,
            DemandEstimateDB.scan_run_id == scan_run_id,
        )
    )
    conclusion = session.scalar(
        select(ConclusionDB).where(
            ConclusionDB.property_id == pid,
            ConclusionDB.scan_run_id == scan_run_id,
        )
    )
    reviews = session.scalars(
        select(ReviewQueueDB).where(
            ReviewQueueDB.property_id == pid,
            ReviewQueueDB.scan_run_id == scan_run_id,
        )
    ).all()
    lines = [
        f"Property: {property_row.canonical_name}",
        f"Country: {property_row.country}",
        f"City: {property_row.city}",
        f"Scene: {property_row.scene_type}",
    ]
    lines.extend(_evidence_line(item) for item in evidence)
    if scene:
        lines.append(
            "Scene model: "
            f"proxy {scene.proxy_level}, "
            f"annual visits estimate {scene.annual_visits_est}, "
            f"basis {scene.proxy_basis}"
        )
    if build:
        lines.append(
            "Build status: "
            f"presence {build.indoor_system_presence}, "
            f"type {build.indoor_system_type}, "
            f"RAT {build.indoor_rat}, "
            f"evidence {build.build_evidence_status}"
        )
    if demand:
        lines.append(
            "Demand: "
            f"busy hour traffic {demand.busy_hour_traffic_gb} GB, "
            f"bandwidth {demand.busy_hour_bandwidth_mbps} Mbps"
        )
    if conclusion:
        lines.append(
            "Conclusion: "
            f"evidence {conclusion.evidence_status}, "
            f"value {conclusion.value_class}, "
            f"action {conclusion.action_class}, "
            f"solution {conclusion.recommended_solution}. "
            f"Reason: {conclusion.reason_to_recommend}. "
            f"Next action: {conclusion.next_action}"
        )
    lines.extend(f"Review action: {review.next_action}" for review in reviews)
    first_evidence = evidence[0] if evidence else None
    return _DocumentPayload(
        source_kind="property_packet",
        source_id=f"{scan_run_id}:{pid}",
        title=property_row.canonical_name,
        content_text="\n".join(lines),
        source_url=first_evidence.source_url if first_evidence else None,
        source_name=first_evidence.source_name if first_evidence else None,
        source_tier=first_evidence.source_tier if first_evidence else None,
        source_date=first_evidence.source_date if first_evidence else None,
        fetched_at=first_evidence.fetched_at if first_evidence else None,
        property_id=pid,
        scan_run_id=scan_run_id,
        country=property_row.country,
        city=property_row.city,
        scene_type=property_row.scene_type,
        field_group=first_evidence.field_group if first_evidence else None,
        indicator_name=first_evidence.indicator_name if first_evidence else None,
    )


def _evidence_line(item: EvidenceItemDB) -> str:
    return (
        f"Evidence {item.field_group}: {item.field_value} "
        f"({item.source_name}, {item.source_tier}, {item.source_date})"
    )


def _document_row(payload: _DocumentPayload) -> RagDocumentDB:
    return RagDocumentDB(
        id=str(uuid4()),
        source_kind=payload.source_kind,
        source_id=payload.source_id,
        content_hash=payload.content_hash,
        title=payload.title,
        source_url=payload.source_url,
        source_name=payload.source_name,
        source_tier=payload.source_tier,
        source_date=payload.source_date,
        fetched_at=payload.fetched_at,
        raw_evidence_id=payload.raw_evidence_id,
        property_id=payload.property_id,
        scan_run_id=payload.scan_run_id,
        country=payload.country,
        city=payload.city,
        scene_type=payload.scene_type,
        metadata_json=payload.metadata,
    )


def _apply_document_payload(row: RagDocumentDB, payload: _DocumentPayload) -> None:
    row.title = payload.title
    row.source_url = payload.source_url
    row.source_name = payload.source_name
    row.source_tier = payload.source_tier
    row.source_date = payload.source_date
    row.fetched_at = payload.fetched_at
    row.raw_evidence_id = payload.raw_evidence_id
    row.property_id = payload.property_id
    row.scan_run_id = payload.scan_run_id
    row.country = payload.country
    row.city = payload.city
    row.scene_type = payload.scene_type
    row.metadata_json = payload.metadata


def _chunk_row(
    document: RagDocumentDB,
    payload: _DocumentPayload,
    index: int,
    text: str,
    embedding: list[float],
    embedding_model: str,
) -> RagChunkDB:
    return RagChunkDB(
        id=str(uuid4()),
        document_id=document.id,
        chunk_index=index,
        chunk_text=text,
        embedding=embedding,
        embedding_model=embedding_model,
        embedding_dim=len(embedding),
        token_count=len(re.findall(r"[\w']+", text)),
        source_url=payload.source_url,
        source_name=payload.source_name,
        source_tier=payload.source_tier,
        source_date=payload.source_date,
        fetched_at=payload.fetched_at,
        raw_evidence_id=payload.raw_evidence_id,
        property_id=payload.property_id,
        scan_run_id=payload.scan_run_id,
        country=payload.country,
        city=payload.city,
        scene_type=payload.scene_type,
        field_group=payload.field_group,
        indicator_name=payload.indicator_name,
    )


def _chunk_text(text: str, *, max_chars: int = 1200) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]
    chunks: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + max_chars)
        chunks.append(clean[start:end].strip())
        start = end
    return chunks


def _chunk_score(chunk: RagChunkDB, query_embedding: list[float], question: str) -> float:
    score = _cosine(chunk.embedding, query_embedding)
    if chunk.source_tier == "Tier 1":
        score += 0.08
    elif chunk.source_tier == "Tier 2":
        score += 0.04
    question_text = question.casefold()
    if chunk.field_group and chunk.field_group.replace("_", " ") in question_text:
        score += 0.05
    if chunk.property_id:
        score += 0.02
    return score


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    numerator = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
    return numerator / (left_norm * right_norm)


def _citation(chunk: RagChunkDB) -> RagCitation:
    return RagCitation(
        chunk_id=chunk.id,
        source_url=chunk.source_url,
        source_name=chunk.source_name,
        source_tier=chunk.source_tier,
        source_date=chunk.source_date,
        fetched_at=chunk.fetched_at.isoformat() if chunk.fetched_at else None,
        raw_evidence_id=chunk.raw_evidence_id,
        property_id=chunk.property_id,
        field_group=chunk.field_group,
        excerpt=chunk.chunk_text[:360],
    )


def _answer(
    question: str,
    chunks: list[RagChunkDB],
    recommendations: list[RagPriorityRecommendation],
) -> str:
    cited_sources = {chunk.source_url for chunk in chunks if chunk.source_url}
    if not cited_sources:
        return (
            "I found indexed text, but it has no auditable source URL. I cannot make "
            "a strong recommendation until source-backed evidence is indexed."
        )
    if recommendations:
        names = ", ".join(item.property_name for item in recommendations[:3])
        return (
            "Based on indexed field-level evidence, the current evidence-backed "
            f"priority candidates are: {names}. Treat this as a cited recommendation "
            "band, not a unified score; review actions remain part of the decision chain."
        )
    best = chunks[0]
    return (
        "Based on indexed evidence, the strongest matching source is "
        f"{best.source_name or best.source_url}. I can summarize evidence and gaps, "
        "but I will not upgrade value or build-status confidence without cited fields."
    )


def _priority_band(conclusion: ConclusionDB | None) -> str:
    if conclusion is None:
        return "Review Needed"
    value = conclusion.value_class
    action = conclusion.action_class
    if value in {"National Flagship", "City Core"} and action in {
        "Direct Recommend",
        "Survey First",
    }:
        return "Evidence-backed Priority"
    if value in {"National Flagship", "City Core", "Regional Anchor"}:
        return "High Value / Validate Build Status"
    return "Review Needed"


def _latest_for_property(
    session: Session,
    table: type[ConclusionDB],
    property_id: str,
) -> Any | None:
    return session.scalar(
        select(table)
        .where(table.property_id == property_id)
        .order_by(table.created_at.desc())
    )


def _latest_reviews(session: Session, property_id: str) -> list[ReviewQueueDB]:
    return list(
        session.scalars(
            select(ReviewQueueDB)
            .where(ReviewQueueDB.property_id == property_id)
            .order_by(ReviewQueueDB.created_at.desc())
        ).all()
    )
