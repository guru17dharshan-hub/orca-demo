"""ORCA risk rule set — the ONLY place safety thresholds live.

Guide §15: 'Do not invent marine safety thresholds.' Each band below is anchored
to a published scale and cites it. The mapping of those scale categories to
ORCA risk levels is ORCA's prototype decision policy (versioned below) and must
be validated with INCOIS/IMD domain experts before operational use. Variables
without an adopted, citable threshold are shown as evidence but NOT scored.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    SEVERE = "SEVERE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


RANK = {RiskLevel.LOW: 0, RiskLevel.MODERATE: 1, RiskLevel.HIGH: 2, RiskLevel.SEVERE: 3}

RULESET_VERSION = "orca-rules-0.2.1"


@dataclass(frozen=True)
class Band:
    level: RiskLevel
    lower: float  # inclusive
    upper: float  # exclusive
    label: str


@dataclass(frozen=True)
class Rule:
    variable: str
    unit: str
    bands: tuple[Band, ...]
    reference: str
    required: bool = False

    def classify(self, value: float) -> Band:
        for band in self.bands:
            if band.lower <= value < band.upper:
                return band
        return self.bands[-1]


INF = float("inf")

WAVE_RULE = Rule(
    variable="wave_height",
    unit="m",
    required=True,
    reference="WMO sea-state code (WMO code table 3700), significant wave height",
    bands=(
        Band(RiskLevel.LOW, 0.0, 1.25, "calm to slight sea (WMO sea state 0–3)"),
        Band(RiskLevel.MODERATE, 1.25, 2.5, "moderate sea (WMO sea state 4)"),
        Band(RiskLevel.HIGH, 2.5, 4.0, "rough sea (WMO sea state 5)"),
        Band(RiskLevel.SEVERE, 4.0, INF, "very rough or worse (WMO sea state ≥6)"),
    ),
)

WIND_RULE = Rule(
    variable="wind_speed",
    unit="km/h",
    required=True,
    reference="Beaufort wind force scale (WMO), sustained 10 m wind",
    bands=(
        Band(RiskLevel.LOW, 0.0, 29.0, "up to moderate breeze (Beaufort ≤4)"),
        Band(RiskLevel.MODERATE, 29.0, 39.0, "fresh breeze (Beaufort 5)"),
        Band(RiskLevel.HIGH, 39.0, 62.0, "strong breeze to near gale (Beaufort 6–7)"),
        Band(RiskLevel.SEVERE, 62.0, INF, "gale or stronger (Beaufort ≥8)"),
    ),
)

# Visibility: lower is worse. Bands expressed on the value axis, worst first.
VISIBILITY_RULE = Rule(
    variable="visibility",
    unit="m",
    reference="Marine-forecast visibility terms (very poor < 1000 m; poor 1000 m–2 NM), e.g. UK Met Office Shipping Forecast glossary",
    bands=(
        Band(RiskLevel.HIGH, 0.0, 1000.0, "very poor visibility (< 1 km, fog)"),
        Band(RiskLevel.MODERATE, 1000.0, 3704.0, "poor visibility (1 km – 2 NM)"),
        Band(RiskLevel.LOW, 3704.0, INF, "moderate or good visibility"),
    ),
)

SCORED_RULES: tuple[Rule, ...] = (WAVE_RULE, WIND_RULE, VISIBILITY_RULE)

THUNDERSTORM_RULE = {
    "variable": "weather_code",
    "codes": (95, 96, 99),
    "level": RiskLevel.HIGH,
    "label": "thunderstorm forecast — lightning hazard",
    "reference": "WMO present-weather code table 4677 (codes 95/96/99 = thunderstorm)",
}

# CAP 1.2 severity is set by the issuing agency (e.g. IMD); ORCA maps it one-to-one.
ADVISORY_SEVERITY = {
    "Extreme": RiskLevel.SEVERE,
    "Severe": RiskLevel.HIGH,
    "Moderate": RiskLevel.MODERATE,
    "Minor": RiskLevel.LOW,
}
# An official warning with 'Unknown' (or unrecognised) severity still covers the point: never drop it silently.
ADVISORY_SEVERITY_DEFAULT = RiskLevel.MODERATE
ADVISORY_REFERENCE ="OASIS CAP 1.2 <severity> as issued by the warning agency; point-in-polygon on the alert area"

# IMD draws 'along and off the coast' warning polygons coarsely: during Cyclone Tauktae the
# Maharashtra–Goa fishermen warning ended about 40 km off the Goa coast, leaving near-shore boats
# outside it. ORCA therefore treats official warnings whose area or text names a coast as covering
# points within this distance of the polygon. Safety-conservative policy; validate with IMD.
COASTAL_WARNING_BUFFER_KM = 50.0

NOT_SCORED = {
    "wind_gusts": "shown as evidence; no citable gust threshold adopted in this rule set yet",
    "swell_wave_height": "already contained in significant wave height",
    "current_speed": "shown as evidence; no citable threshold adopted yet",
    "sea_level": "tide/sea level shown for planning; not a safety factor",
    "precipitation": "rain shown as evidence; visibility and thunderstorm rules capture its hazard",
    "cape": "convective potential shown as evidence; thunderstorm forecast is scored instead",
}

LEAD_TIME_CONFIDENCE_HOURS = 72  # beyond this, forecasts are flagged as lower confidence


def rules_table() -> dict:
    """Machine-readable rule set for the UI 'How is safety calculated?' panel and docs."""
    return {
        "version": RULESET_VERSION,
        "combination": "Hourly level = worst of all scored factors; official advisories covering the point raise the level "
        "per their CAP severity. Missing wave height or wind makes the hour INSUFFICIENT_DATA unless another factor is "
        "already HIGH or SEVERE. Window level = worst hour.",
        "scored": [
            {
                "variable": r.variable,
                "unit": r.unit,
                "required": r.required,
                "reference": r.reference,
                "bands": [
                    {"level": b.level.value, "from": b.lower, "to": None if b.upper == INF else b.upper, "label": b.label}
                    for b in r.bands
                ],
            }
            for r in SCORED_RULES
        ]
        + [
            {
                "variable": THUNDERSTORM_RULE["variable"],
                "codes": list(THUNDERSTORM_RULE["codes"]),
                "level": THUNDERSTORM_RULE["level"].value,
                "label": THUNDERSTORM_RULE["label"],
                "reference": THUNDERSTORM_RULE["reference"],
            },
            {
                "variable": "advisory",
                "mapping": {k: v.value for k, v in ADVISORY_SEVERITY.items()} | {"Unknown/other": ADVISORY_SEVERITY_DEFAULT.value},
                "reference": ADVISORY_REFERENCE,
                "coastal_buffer_km": COASTAL_WARNING_BUFFER_KM,
                "note": "Official warnings that name a coast also cover points within this distance of their polygon "
                "(IMD coastal polygons can stop tens of km offshore). Model-derived cyclone watches are shown but not scored.",
            },
        ],
        "not_scored": NOT_SCORED,
        "disclaimer": "Decision support only — not a guarantee of safety. The mapping of published scales to ORCA risk "
        "levels is a prototype policy that must be validated with INCOIS/IMD before operational use.",
    }
