from __future__ import annotations

from urllib.parse import quote


def make_google_maps_link(
    latitude: float,
    longitude: float,
    *,
    property_name: str | None = None,
    city: str | None = None,
    country: str | None = None,
) -> str:
    query_parts = [
        str(part).strip()
        for part in [property_name, city, country]
        if str(part or "").strip()
    ]
    if not query_parts:
        return f"https://www.google.com/maps/search/?api=1&query={latitude:.6f},{longitude:.6f}"
    query = ", ".join(query_parts)
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


def short_reason(
    scene_type: str,
    main_metric_text: str,
    solution: str,
    inference_used: bool = False,
) -> str:
    suffix = "（部分依据推测，待核验）" if inference_used else ""
    templates = {
        "airport_terminal": "门户机场，{metric}，航站楼室内连续覆盖需求明确，推荐{solution}。",
        "convention_center": "会展场馆，{metric}，短时高并发通信需求突出，推荐{solution}。",
        "stadium": "大型体育场，{metric}，半开放高峰场景适合{solution}。",
        "luxury_hotel_mice": "高端会奖酒店，{metric}，商务与会务覆盖需求突出，推荐{solution}。",
        "mall_mixed_use": "旗舰商业综合体，{metric}，周末与节假日峰值明显，推荐{solution}。",
        "office_government": (
            "核心办公/政府楼宇，{metric}，深室内连续覆盖价值明确，推荐{solution}。"
        ),
        "hospital": "大型医院，{metric}，连续可靠室内覆盖需求强，推荐{solution}。",
        "university": "核心高校场景，{metric}，校园高密度室内需求明显，推荐{solution}。",
        "transport_hub": "核心换乘枢纽，{metric}，站厅/通道连续覆盖必要性高，推荐{solution}。",
    }
    template = templates.get(scene_type, "高价值候选场景，{metric}，推荐{solution}。")
    return template.format(metric=main_metric_text, solution=solution) + suffix
