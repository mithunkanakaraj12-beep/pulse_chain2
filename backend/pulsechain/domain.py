"""Static domain definitions for the PulseChain AI demo.

Everything here is *illustrative*. The treatment protocol rates (units of medicine used per
reported case) are simplified planning constants, NOT clinical guidance. In a real deployment they
would come from national treatment guidelines and the clinic's own dispensing logs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

DATA_LABEL = "SIMULATED DATA - illustrative demo dataset, not real epidemiological or inventory data"

# The demo clock is frozen so every run, test and screenshot is reproducible.
# as_of = Monday of the last observed week. "today" (the demo day) is the following Monday.
DEFAULT_AS_OF = date(2026, 5, 18)
FORECAST_MAX_WEEKS = 8
HISTORY_YEARS = 5


def demo_today(as_of: date) -> date:
    return as_of + timedelta(days=7)


@dataclass(frozen=True)
class Disease:
    id: str
    name: str
    description: str


DISEASES: list[Disease] = [
    Disease("malaria", "Malaria", "Vector-borne; transmission follows rainfall and standing water with a 2-6 week lag."),
    Disease("cholera", "Cholera", "Water-borne; outbreaks follow flooding and heavy rain within 1-3 weeks."),
    Disease("pneumonia", "Pneumonia", "Respiratory; peaks in the cooler season, only weakly rain related."),
]
DISEASE_IDS = [d.id for d in DISEASES]


@dataclass(frozen=True)
class Medicine:
    id: str
    name: str
    short_name: str
    unit: str
    order_multiple: int
    shelf_life_days: int
    requires_cold_chain: bool
    # Routine weekly use per 1,000 catchment population NOT driven by the three modelled diseases.
    baseline_weekly_per_1000: float


MEDICINES: list[Medicine] = [
    Medicine("act", "Artemether-Lumefantrine 20/120 mg", "malaria treatment (ACT)", "courses", 10, 730, False, 0.10),
    Medicine("rdt", "Malaria Rapid Diagnostic Test", "malaria RDT kits", "tests", 25, 540, False, 0.80),
    Medicine("ors", "Oral Rehydration Salts", "ORS sachets", "sachets", 50, 1095, False, 5.00),
    Medicine("ringer", "Ringer's Lactate IV 500 ml", "IV Ringer's Lactate", "bags", 10, 730, False, 0.20),
    Medicine("doxy", "Doxycycline 100 mg", "doxycycline", "courses", 10, 730, False, 0.40),
    Medicine("amox", "Amoxicillin DT 250 mg", "amoxicillin", "courses", 10, 730, False, 1.50),
]
MEDICINE_BY_ID = {m.id: m for m in MEDICINES}

# Units of medicine consumed per reported case of the disease (simplified protocol).
# medicine_id -> {disease_id: units_per_case}
TREATMENT_PROTOCOL: dict[str, dict[str, float]] = {
    "act": {"malaria": 0.85},
    "rdt": {"malaria": 1.60},
    "ors": {"cholera": 9.0},
    "ringer": {"cholera": 1.2},
    "doxy": {"cholera": 0.55},
    "amox": {"pneumonia": 1.0},
}


def medicines_for_disease(disease_id: str) -> list[str]:
    return [m for m, d in TREATMENT_PROTOCOL.items() if disease_id in d]


@dataclass(frozen=True)
class ClinicSeed:
    id: str
    name: str
    region: str
    lat: float
    lon: float
    catchment_pop: int
    lead_time_days: int
    road_speed_kmh: float
    health_worker: str
    stock_profile: str


REGIONS = {
    "highland": "Highland District",
    "riverine": "Riverine District",
    "coastal": "Coastal Plains",
}

# Clinic names are fictional. Coordinates fall in an illustrative eastern-India-like area
# (a monsoon, malaria- and cholera-endemic climate) and do not identify real facilities.
CLINICS: list[ClinicSeed] = [
    ClinicSeed("C01", "Bandhapada Health Post", "highland", 20.30, 83.95, 9000, 16, 24, "Anita Mohanty", "tight"),
    ClinicSeed("C02", "Rengal Sub-Centre", "highland", 20.42, 84.10, 14000, 14, 28, "Ravi Naik", "normal"),
    ClinicSeed("C03", "Tikarpada PHC", "highland", 20.20, 84.20, 22000, 10, 32, "Dr. Sunita Behera", "surplus"),
    ClinicSeed("C04", "Mahulpali Dispensary", "highland", 20.52, 83.85, 7500, 21, 22, "Prakash Sahu", "tight"),
    ClinicSeed("C05", "Chandpur Rural Clinic", "riverine", 20.85, 84.75, 18000, 14, 30, "Meera Pradhan", "tight"),
    ClinicSeed("C06", "Nuasahi Health Post", "riverine", 20.95, 84.95, 11000, 17, 26, "Bibhuti Das", "normal"),
    ClinicSeed("C07", "Baghmari PHC", "riverine", 20.75, 85.05, 26000, 10, 34, "Dr. Kamal Otta", "surplus"),
    ClinicSeed("C08", "Kuliasar Sub-Centre", "riverine", 21.05, 84.65, 8500, 20, 22, "Sasmita Jena", "tight"),
    ClinicSeed("C09", "Sundarpur Clinic", "coastal", 20.15, 85.85, 20000, 12, 32, "Lopamudra Rath", "tight"),
    ClinicSeed("C10", "Pentakota Health Post", "coastal", 20.30, 86.05, 12000, 15, 28, "Gopal Swain", "normal"),
    ClinicSeed("C11", "Gopalnagar PHC", "coastal", 20.05, 86.10, 28000, 9, 34, "Dr. Rina Samal", "surplus"),
    ClinicSeed("C12", "Jamunabil Dispensary", "coastal", 20.38, 85.70, 9500, 18, 24, "Tapan Muduli", "normal"),
]
CLINIC_BY_ID = {c.id: c for c in CLINICS}


def fictional_phone(index: int) -> str:
    """+1 202 555-01XX numbers are reserved for fiction, so no real person can be messaged by accident."""
    return f"+1202555{100 + index:04d}"


def week_starts(as_of: date, n_weeks: int) -> list[date]:
    """The `n_weeks` Mondays ending at (and including) as_of, oldest first."""
    return [as_of - timedelta(weeks=n_weeks - 1 - i) for i in range(n_weeks)]
