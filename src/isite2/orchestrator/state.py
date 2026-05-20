from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from isite2.domain.models import ReviewItem, ScanScope, SitePacket


class PipelineEvent(BaseModel):
    step_key: str
    status: str
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PipelineState(BaseModel):
    state_id: UUID = Field(default_factory=uuid4)
    scope: ScanScope
    candidates: list[dict] = Field(default_factory=list)
    packets: list[SitePacket] = Field(default_factory=list)
    review_items: list[ReviewItem] = Field(default_factory=list)
    events: list[PipelineEvent] = Field(default_factory=list)

    def record(self, step_key: str, status: str, message: str) -> None:
        self.events.append(PipelineEvent(step_key=step_key, status=status, message=message))
