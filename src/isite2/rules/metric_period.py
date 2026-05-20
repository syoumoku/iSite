from __future__ import annotations

import re


ANNUALIZED_METRIC_FIELDS = {
    "annual_passenger_throughput",
    "annual_footfall",
    "annual_visits",
}


def annual_metric_period_issue(
    field_group: str | None,
    field_value: str | None,
    *,
    context: str | None = None,
) -> str | None:
    """Return a blocking issue when an annual metric uses a non-annual grain.

    This is intentionally conservative: it blocks clear quarter/month/day values
    from masquerading as annual metrics, while allowing direct annual totals and
    deterministic annual aggregations such as Q1+Q2+Q3+Q4.
    """

    normalized_field = _normalize_field(field_group)
    if normalized_field not in ANNUALIZED_METRIC_FIELDS:
        return None

    text = _normalize(" ".join(part for part in [field_value, context] if part))
    if not text:
        return None

    if _has_annual_aggregation_marker(text):
        return None
    if _has_nonannual_period_marker(text):
        return (
            "annual metric appears to use a non-annual period value without "
            "explicit annual aggregation"
        )
    if _has_direct_annual_marker(text):
        return None
    if _has_chart_only_marker(text):
        return (
            "annual metric is chart-derived without explicit annual total or "
            "aggregation method"
        )
    return None


def _has_annual_aggregation_marker(text: str) -> bool:
    has_all_quarters = all(f"q{quarter}" in text for quarter in range(1, 5))
    if has_all_quarters and any(
        marker in text for marker in ["annual", "sum", "summing", "summed", "derived"]
    ):
        return True
    if re.search(r"\bsumm\w*\b(?:\s+\w+){0,4}\s+quarterly\b", text):
        return True
    aggregation_markers = [
        "sum of quarterly",
        "sum of q1 q2 q3 q4",
        "q1 q2 q3 q4",
        "q1+q2+q3+q4",
        "summing quarterly",
        "summed quarterly",
        "four quarters",
        "sum of monthly",
        "jan feb mar apr may jun jul aug sep oct nov dec",
        "annualized by",
        "annualised by",
        "x12",
        "x 12",
        "x365",
        "x 365",
    ]
    return any(marker in text for marker in aggregation_markers)


def _has_nonannual_period_marker(text: str) -> bool:
    patterns = [
        r"\bq[1-4]\b",
        r"\b[1-4](st|nd|rd|th)\s+quarter\b",
        r"\bquarter(ly)?\b",
        r"\bmonth(ly)?\b",
        r"\baverage\s+per\s+(day|week|month)\b",
        r"\bper\s+(day|week|month)\b",
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _has_direct_annual_marker(text: str) -> bool:
    if re.search(r"\b(passengers?|visitors?|visits?|pax)\s*/\s*year\b", text):
        return True
    if "per year" in text or "per annum" in text:
        return True
    if "annual" in text or "full year" in text or "calendar year" in text:
        return True
    if re.search(r"\b(year to date|year-to-date|ytd)\b", text) and re.search(
        r"\b(dec|december|12)\b",
        text,
    ):
        return True
    return False


def _has_chart_only_marker(text: str) -> bool:
    return "chart" in text or "statistics averages" in text


def _normalize(value: str | None) -> str:
    text = str(value or "").casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[_/|,;:()\[\]{}=+-]+", " ", text)
    text = re.sub(r"[^a-z0-9%\s.-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_field(value: str | None) -> str:
    text = str(value or "").casefold().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")
