from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from isite2.domain.models import EvidenceItem, ReviewItem, SitePacket
from isite2.growth.models import (
    FreshnessTask,
    HumanFeedbackRecord,
    InferenceCorrection,
    RuleVersion,
    SourceReliabilityScore,
)
from isite2.rules.validation import is_concrete_review_action


class ReviewTransitionError(ValueError):
    pass


class GrowthLoopService:
    def __init__(self) -> None:
        self.feedback_records: list[HumanFeedbackRecord] = []
        self.rule_versions: list[RuleVersion] = []
        self.freshness_tasks: list[FreshnessTask] = []
        self.inference_corrections: list[InferenceCorrection] = []
        self.source_scores: dict[str, SourceReliabilityScore] = {}

    def close_review_item(
        self,
        review: ReviewItem,
        evidence: EvidenceItem | None = None,
        note: str | None = None,
    ) -> ReviewItem:
        if not is_concrete_review_action(review.next_action):
            raise ReviewTransitionError("review item next_action must be concrete before closing")
        if evidence is None and not note:
            raise ReviewTransitionError("closing review item requires evidence or a review note")
        review.status = "closed"
        return review

    def attach_feedback(
        self,
        field_name: str,
        new_value: str,
        property_id: UUID | None = None,
        old_value: str | None = None,
        reviewer: str | None = None,
        note: str | None = None,
    ) -> HumanFeedbackRecord:
        record = HumanFeedbackRecord(
            property_id=property_id,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reviewer=reviewer,
            note=note,
        )
        self.feedback_records.append(record)
        return record

    def register_rule_version(
        self,
        version: str,
        description: str,
        config_snapshot: dict | None = None,
    ) -> RuleVersion:
        rule_version = RuleVersion(
            version=version,
            description=description,
            config_snapshot=config_snapshot or {},
        )
        self.rule_versions.append(rule_version)
        return rule_version

    def enqueue_freshness_rescan(
        self,
        source_url: str,
        reason: str,
        days_until_due: int = 30,
    ) -> FreshnessTask:
        task = FreshnessTask(
            source_url=source_url,
            reason=reason,
            due_at=datetime.now(UTC) + timedelta(days=days_until_due),
        )
        self.freshness_tasks.append(task)
        return task

    def record_inference_correction(
        self,
        packet: SitePacket,
        inferred_field: str,
        corrected_value: str,
        evidence_url: str | None = None,
    ) -> InferenceCorrection:
        old_value = ""
        for inference in packet.inference:
            if inference.inferred_field == inferred_field:
                old_value = inference.inferred_value
        correction = InferenceCorrection(
            property_id=packet.entity.property_id,
            inferred_field=inferred_field,
            old_value=old_value,
            corrected_value=corrected_value,
            evidence_url=evidence_url,
        )
        self.inference_corrections.append(correction)
        return correction

    def validate_main_metric_update(self, packet: SitePacket, new_metric: str) -> list[str]:
        issues: list[str] = []
        if not packet.evidence:
            issues.append("更新 G 列主指标前必须补充证据表记录。")
        if "忙时流量" in new_metric or "busy" in new_metric.lower():
            issues.append("G 列物业点重要证据不能使用忙时流量或二次 proxy 结果。")
        if "（推测）" in new_metric and not packet.inference:
            issues.append("G 列使用推测值时必须同步写入推测留痕。")
        return issues

    def update_source_reliability(
        self,
        source_name: str,
        source_tier: str,
        success: bool,
        conflict: bool = False,
    ) -> SourceReliabilityScore:
        score = self.source_scores.get(
            source_name,
            SourceReliabilityScore(source_name=source_name, source_tier=source_tier),
        )
        if success:
            score.successful_extractions += 1
        else:
            score.failed_extractions += 1
        if conflict:
            score.conflict_count += 1
        total = score.successful_extractions + score.failed_extractions + score.conflict_count
        score.score = max(0.0, min(1.0, (score.successful_extractions + 0.5) / (total + 1)))
        self.source_scores[source_name] = score
        return score
