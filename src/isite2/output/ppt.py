from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt

from isite2.domain.models import SitePacket
from isite2.rules.candidate_quality import filter_packets_for_surface


def create_ppt_deck(
    packets: list[SitePacket],
    title: str = "iSite2 Insight Cards",
    include_blocked_quality: bool = False,
) -> Presentation:
    packets = filter_packets_for_surface(
        packets,
        "export",
        include_blocked_quality=include_blocked_quality,
    )
    deck = Presentation()
    _add_title_slide(deck, title, f"{len(packets)} candidate sites")
    _add_logic_slide(deck)
    _add_snapshot_slide(deck, packets)
    for packet in packets:
        _add_site_card(deck, packet)
    return deck


def write_ppt_deck(
    path: Path,
    packets: list[SitePacket],
    title: str = "iSite2 Insight Cards",
    include_blocked_quality: bool = False,
) -> Path:
    deck = create_ppt_deck(packets, title, include_blocked_quality)
    path.parent.mkdir(parents=True, exist_ok=True)
    deck.save(path)
    return path


def _add_title_slide(deck: Presentation, title: str, subtitle: str) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[0])
    slide.shapes.title.text = title
    slide.placeholders[1].text = subtitle


def _add_logic_slide(deck: Presentation) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Rule / Advancement Logic"
    slide.placeholders[1].text = (
        "Evidence -> Scene Model -> Build Status -> Demand -> Inference -> "
        "Conclusion -> Review Queue. Build status is judged independently from property value."
    )


def _add_snapshot_slide(deck: Presentation, packets: list[SitePacket]) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Portfolio Snapshot"
    direct = sum(packet.conclusion.action_class == "Direct Recommend" for packet in packets)
    survey = sum(packet.conclusion.action_class == "Survey First" for packet in packets)
    review = sum(len(packet.review_queue) for packet in packets)
    slide.placeholders[1].text = (
        f"Candidates: {len(packets)}\n"
        f"Direct Recommend: {direct}\n"
        f"Survey First: {survey}\n"
        f"Review Items: {review}"
    )


def _add_site_card(deck: Presentation, packet: SitePacket) -> None:
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    entity = packet.entity
    demand = packet.demand
    title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.35), Inches(9.0), Inches(0.5))
    title_frame = title_box.text_frame
    title_frame.text = entity.property_name
    title_frame.paragraphs[0].font.size = Pt(24)
    title_frame.paragraphs[0].font.bold = True

    body = slide.shapes.add_textbox(Inches(0.5), Inches(1.0), Inches(9.0), Inches(5.8))
    body.text_frame.text = "\n".join(
        [
            f"Location: {entity.country} / {entity.city}",
            f"Scene: {entity.scene_type}",
            f"Recommended Solution: {packet.conclusion.recommended_solution}",
            (
                "Status: "
                f"{packet.conclusion.evidence_status} / "
                f"{packet.conclusion.value_class} / "
                f"{packet.conclusion.action_class}"
            ),
            f"Core Metric: {packet.evidence[0].field_value if packet.evidence else 'Unknown'}",
            (
                "Busy Hour: "
                f"users={_fmt(demand.busy_hour_users if demand else None)}, "
                f"traffic_gb={_fmt(demand.busy_hour_traffic_gb if demand else None)}, "
                f"bandwidth_mbps={_fmt(demand.busy_hour_bandwidth_mbps if demand else None)}"
            ),
            f"Area / Scale: {packet.scene.area_metric_name} ({packet.scene.area_metric_status})",
            f"Coordinates: {entity.latitude}, {entity.longitude} ({entity.geocode_precision})",
            (
                "Build Status: "
                f"{packet.build_status.indoor_system_presence} / "
                f"{packet.build_status.indoor_system_type} / {packet.build_status.indoor_rat}"
            ),
            f"Value Judgment: {packet.conclusion.reason_to_recommend}",
            f"Risk / Next Action: {packet.conclusion.next_action}",
            f"Evidence Entries: {len(packet.evidence)}",
        ]
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "Unknown"
    return f"{value:.2f}"
