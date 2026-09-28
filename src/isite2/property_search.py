from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


def normalize_property_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).casefold()
    characters: list[str] = []
    last_base = ""
    for character in normalized:
        if unicodedata.combining(character):
            if last_base and "LATIN" in unicodedata.name(last_base, ""):
                continue
            characters.append(character)
            continue
        characters.append(character)
        last_base = character
    without_marks = unicodedata.normalize("NFC", "".join(characters))
    return " ".join(re.sub(r"[\W_]+", " ", without_marks, flags=re.UNICODE).split())


def property_search_document(property_name: str, aliases: Iterable[str]) -> str:
    values = [property_name, *aliases]
    normalized = [normalize_property_search_text(value) for value in values]
    return "\n".join(dict.fromkeys(value for value in normalized if value))


def rank_property_search_rows(
    rows: Iterable[Mapping[str, Any]],
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_query = normalize_property_search_text(query)
    if not normalized_query:
        return []

    matches: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in rows:
        property_name = str(row.get("property_name") or "")
        aliases = [str(value) for value in row.get("aliases", []) or [] if str(value).strip()]
        candidates = [(property_name, "canonical"), *((alias, "alias") for alias in aliases)]
        best: tuple[int, str, str] | None = None
        for candidate, source in candidates:
            normalized_candidate = normalize_property_search_text(candidate)
            if not normalized_candidate or normalized_query not in normalized_candidate:
                continue
            if normalized_candidate == normalized_query:
                rank = 0 if source == "canonical" else 1
                relation = "exact"
            elif normalized_candidate.startswith(normalized_query):
                rank = 2 if source == "canonical" else 3
                relation = "prefix"
            else:
                rank = 4 if source == "canonical" else 5
                relation = "contains"
            candidate_match = (rank, candidate, f"{source}_{relation}")
            if best is None or (candidate_match[0], len(candidate_match[1])) < (
                best[0],
                len(best[1]),
            ):
                best = candidate_match
        if best is None:
            continue
        rank, matched_name, match_type = best
        result = {
            "property_id": str(row["property_id"]),
            "property_name": property_name,
            "matched_name": matched_name,
            "match_type": match_type,
            "country": str(row.get("country") or ""),
            "city": str(row.get("city") or ""),
            "scene_type": str(row.get("scene_type") or ""),
        }
        sort_key = (
            rank,
            len(normalize_property_search_text(matched_name)),
            normalize_property_search_text(property_name),
            result["country"].casefold(),
            result["city"].casefold(),
            result["property_id"],
        )
        matches.append((sort_key, result))
    matches.sort(key=lambda item: item[0])
    return [result for _sort_key, result in matches[:limit]]
