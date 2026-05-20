from __future__ import annotations

import re

from isite2.connectors.models import EvidenceExtractionResult, FetchedPage
from isite2.rules.metric_period import annual_metric_period_issue

INDICATOR_PATTERNS = {
    "annual_passenger_throughput": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(million\s+|millions?\s+|milhões\s+|millones\s+|مليون\s+)?"
        r"(passengers|passenger movements|passageiros|pasajeros|passagers|pax|"
        r"ركاب|مسافر(?:ين)?|人次)",
        re.IGNORECASE,
    ),
    "passenger_throughput": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(million\s+|millions?\s+|milhões\s+|millones\s+|مليون\s+)?"
        r"(passengers|cruise passengers|passageiros|pasajeros|passagers|pax|"
        r"ركاب|مسافر(?:ين)?)",
        re.IGNORECASE,
    ),
    "terminal_capacity": re.compile(
        r"(?:(terminal|airport|designed|capacity|handle).{0,50})?"
        r"(?P<value>\d[\d,.]*)\s*"
        r"(million\s+|millions?\s+|milhões\s+|millones\s+|مليون\s+)?"
        r"(passengers|passageiros|pasajeros|passagers|pax|ركاب|مسافر(?:ين)?)"
        r"(.{0,30}(per year|annually|capacity|handle|por ano|por año|par an|سنويا))?",
        re.IGNORECASE,
    ),
    "international_passenger_share": re.compile(
        r"(?P<value>\d[\d,.]*)\s*%\s*(international passengers|international traffic)",
        re.IGNORECASE,
    ),
    "seat_count": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(seats|seat capacity|capacity|座|asientos|assentos|lugares|places|"
        r"capacidad|capacité|spectators|espectadores|spectateurs|مقاعد|متفرج)",
        re.IGNORECASE,
    ),
    "capacity": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(seats|people|spectators|capacity|asientos|assentos|lugares|places|"
        r"personas|pessoas|personnes|capacidad|capacité|مقاعد|أشخاص|متفرج)",
        re.IGNORECASE,
    ),
    "exhibition_area": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(sqm|m2|㎡|square metres|square meters).{0,40}"
        r"(exhibition|exhibit|convention|exposição|exposición|exposition|congrès|"
        r"معرض|مؤتمر)",
        re.IGNORECASE,
    ),
    "annual_events": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(events|exhibitions|conferences)",
        re.IGNORECASE,
    ),
    "peak_event_capacity": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(delegates|attendees|people|visitors)",
        re.IGNORECASE,
    ),
    "keys": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(keys|guest rooms|rooms|habitaciones|quartos|apartamentos|chambres|غرف)",
        re.IGNORECASE,
    ),
    "rooms": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(rooms|guestrooms|keys|habitaciones|quartos|apartamentos|chambres|غرف)",
        re.IGNORECASE,
    ),
    "meeting_ballroom_area": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(sqm|m2|㎡|square metres|square meters).{0,40}"
        r"(ballroom|meeting|conference|salão|eventos|reuniones|banquetes|réunion|"
        r"conférence|قاعة|اجتماعات)",
        re.IGNORECASE,
    ),
    "ballroom_capacity": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(guests|delegates|attendees).{0,40}(ballroom|conference)",
        re.IGNORECASE,
    ),
    "annual_footfall": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(million\s+|millions?\s+|milhões\s+|millones\s+|مليون\s+)?"
        r"(visitors|footfall|visits|visitantes|visiteurs|visitas|زوار)",
        re.IGNORECASE,
    ),
    "beds": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(beds|床|camas|leitos|lits|أسرة|سرير)",
        re.IGNORECASE,
    ),
    "outpatient_volume": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(outpatients|outpatient visits|patients)",
        re.IGNORECASE,
    ),
    "enrollment": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(students|enrollment|enrolled|人|estudiantes|alumnos|estudantes|alunos|"
        r"étudiants|inscriptions|طلاب)",
        re.IGNORECASE,
    ),
    "students": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(students|learners|estudiantes|alumnos|estudantes|alunos|étudiants|طلاب)",
        re.IGNORECASE,
    ),
    "gla": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(sqm|m2|㎡|square metres|square meters|sq m)",
        re.IGNORECASE,
    ),
    "office_gfa": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(sqm|m2|㎡|square metres|square meters).{0,40}"
        r"(gross floor area|GFA|office)",
        re.IGNORECASE,
    ),
    "office_nla": re.compile(
        r"(?P<value>\d[\d,.]*)\s*(sqm|m2|㎡|square metres|square meters).{0,40}"
        r"(net lettable area|NLA|office)",
        re.IGNORECASE,
    ),
    "daily_ridership": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(daily\s+|diários\s+|diarios\s+|quotidiens\s+|يوميا\s+)?"
        r"(passengers|riders|ridership|passageiros|pasajeros|passagers|voyageurs|ركاب)",
        re.IGNORECASE,
    ),
    "ridership": re.compile(
        r"(?P<value>\d[\d,.]*)\s*"
        r"(million\s+|millions?\s+|milhões\s+|millones\s+|مليون\s+)?"
        r"(passengers|riders|ridership|passageiros|pasajeros|passagers|voyageurs|ركاب)",
        re.IGNORECASE,
    ),
    "line_count": re.compile(r"(?P<value>\d[\d,.]*)\s*(lines|routes|platforms)", re.IGNORECASE),
    "terminal_role": re.compile(
        r"(international gateway|main terminal|hub|terminal)",
        re.IGNORECASE,
    ),
    "main_venue_role": re.compile(
        r"(national stadium|main stadium|host venue|home stadium)",
        re.IGNORECASE,
    ),
    "hub_role": re.compile(r"(interchange|central station|transport hub|terminal)", re.IGNORECASE),
    "campus_center_role": re.compile(
        r"(main campus|flagship campus|central campus)",
        re.IGNORECASE,
    ),
    "indoor_coverage_deployment": re.compile(
        r"(indoor\s+(coverage|network|connectivity)|in-building\s+(coverage|network)|"
        r"distributed\s+antenna\s+system|DAS)",
        re.IGNORECASE,
    ),
    "das_deployment": re.compile(
        r"(distributed\s+antenna\s+system|DAS|small\s+cell|pRRU|hRRU)",
        re.IGNORECASE,
    ),
    "indoor_5g_upgrade": re.compile(
        r"(5G\s+indoor|indoor\s+5G|5G\s+(coverage|network|deployment))",
        re.IGNORECASE,
    ),
}


def extract_first_indicator(
    page: FetchedPage,
    property_name: str,
    preferred_indicators: list[str],
) -> EvidenceExtractionResult:
    return extract_indicators(page, property_name, preferred_indicators)[0]


def extract_indicators(
    page: FetchedPage,
    property_name: str,
    preferred_indicators: list[str],
) -> list[EvidenceExtractionResult]:
    results: list[EvidenceExtractionResult] = []
    seen: set[tuple[str, str]] = set()
    for indicator in preferred_indicators:
        pattern = INDICATOR_PATTERNS.get(indicator)
        if pattern is None:
            continue
        for match in pattern.finditer(page.content_text):
            field_value = match.group(0)
            context = _match_context(page.content_text, match.start(), match.end())
            if annual_metric_period_issue(indicator, field_value, context=context):
                continue
            key = (indicator, field_value.casefold())
            if key in seen:
                continue
            seen.add(key)
            results.append(
                EvidenceExtractionResult(
                    property_name=property_name,
                    field_group=indicator,
                    field_value=field_value,
                    source_url=page.source_url,
                    source_name=page.source_name,
                    source_tier=page.source_tier,
                    source_date=page.source_date,
                )
            )
    if results:
        return results
    return [
        EvidenceExtractionResult(
            property_name=property_name,
            field_group=preferred_indicators[0] if preferred_indicators else "unknown",
            field_value="",
            source_url=page.source_url,
            source_name=page.source_name,
            source_tier=page.source_tier,
            source_date=page.source_date,
            extraction_status="review_required",
            review_reason="未能从公开页面自动提取场景主指标。",
            next_action="人工核验页面正文、年报 PDF 或官方统计页，补充字段级证据。",
        )
    ]


def _match_context(content: str, start: int, end: int, window: int = 140) -> str:
    return content[max(0, start - window) : min(len(content), end + window)]
