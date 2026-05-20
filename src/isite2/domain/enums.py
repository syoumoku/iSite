from enum import StrEnum


class EvidenceStatus(StrEnum):
    VERIFIED = "Verified"
    SUPPORTED = "Supported"
    INDICATIVE = "Indicative"
    INSUFFICIENT = "Insufficient"


class ValueClass(StrEnum):
    NATIONAL_FLAGSHIP = "National Flagship"
    CITY_CORE = "City Core"
    REGIONAL_ANCHOR = "Regional Anchor"
    LOCAL_CANDIDATE = "Local Candidate"
    OBSERVATION = "Observation"


class ActionClass(StrEnum):
    DIRECT_RECOMMEND = "Direct Recommend"
    SURVEY_FIRST = "Survey First"
    REVIEW_QUEUE = "Review Queue"
    MONITOR = "Monitor"


class SceneForm(StrEnum):
    INDOOR = "Indoor"
    SEMI_OPEN = "Semi-open"


class SourceTier(StrEnum):
    TIER_1 = "Tier 1"
    TIER_2 = "Tier 2"
    TIER_3 = "Tier 3"


class EvidenceType(StrEnum):
    DIRECT = "Direct"
    PROXY = "Proxy"
    INFERRED = "Inferred"


class CrossCheckStatus(StrEnum):
    STRONG = "Strong Cross-check"
    PARTIAL = "Partial Cross-check"
    SINGLE_SOURCE = "Single Source"
    CONFLICT = "Evidence Conflict"
    NOT_CHECKED = "Not Checked"


class ProxyLevel(StrEnum):
    P0_DIRECT = "P0 Direct"
    P1_STRONG = "P1 Strong Proxy"
    P2_MODERATE = "P2 Moderate Proxy"
    P3_WEAK = "P3 Weak Proxy"


class IndoorSystemPresence(StrEnum):
    CONFIRMED_PRESENT = "Confirmed Present"
    CONFIRMED_ABSENT = "Confirmed Absent"
    LIKELY_PRESENT = "Likely Present"
    LIKELY_ABSENT = "Likely Absent"
    NO_PUBLIC_EVIDENCE = "No Public Evidence"
    PLANNED_UNDER_CONSTRUCTION = "Planned / Under Construction"
    UNKNOWN = "Unknown"


class IndoorSystemType(StrEnum):
    TRADITIONAL_DAS = "Traditional DAS"
    DIGITAL_DAS = "Digital DAS"
    HYBRID_DAS = "Hybrid DAS"
    SMALL_CELLS_OTHER = "Small Cells / Other Indoor System"
    UNKNOWN = "Unknown"


class IndoorRAT(StrEnum):
    FOUR_G = "4G"
    FIVE_G = "5G"
    FOUR_G_PLUS_FIVE_G = "4G+5G"
    MULTI_RAT_LEGACY = "Multi-RAT Legacy"
    UNKNOWN = "Unknown"


class BuildEvidenceStatus(StrEnum):
    VERIFIED = "Verified"
    SUPPORTED = "Supported"
    INDICATIVE = "Indicative"
    UNKNOWN = "Unknown"


class RecommendedSolution(StrEnum):
    PRRU = "pRRU"
    HRRU = "hRRU"
    UNKNOWN = "Unknown"
