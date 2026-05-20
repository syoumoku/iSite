from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")

VAGUE_REVIEW_ACTIONS = ("待研究", "待定", "TBD", "todo", "进一步研究")
CONCRETE_REVIEW_VERBS = ("补查", "查询", "核验", "核查", "确认", "比对", "检查")


def display_slice(items: Sequence[T], top_n: int | None = None) -> list[T]:
    if top_n is None or top_n <= 0:
        return list(items)
    return list(items[:top_n])


def full_scan_preserved(candidate_count: int, stored_count: int) -> bool:
    return stored_count >= candidate_count


def is_concrete_review_action(next_action: str) -> bool:
    normalized = next_action.strip()
    if len(normalized) < 8:
        return False
    if any(vague.lower() in normalized.lower() for vague in VAGUE_REVIEW_ACTIONS):
        return False
    return any(verb in normalized for verb in CONCRETE_REVIEW_VERBS)
