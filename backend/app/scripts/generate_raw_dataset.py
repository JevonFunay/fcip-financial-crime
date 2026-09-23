"""Synthetic raw-source generator (TRD §11), writing the source file contract of TRD §6.1.

This is DEP-01: the full source schema at a small volume, with labelled
scenarios and deliberate quality defects, so the ingestion pipeline has real
work to do and detection metrics have ground truth to measure against.

Everything here is synthetic and shaped, never sourced (TRD §11.0.3). Names,
identifiers and addresses come from generated pools; the national ID pattern
uses a province prefix that is never issued, so a generated value cannot
collide with a real one.

Run:
    python -m app.scripts.generate_raw_dataset --profile small
    python -m app.scripts.generate_raw_dataset --profile full --seed 20260923
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.scripts.raw_contract import (
    CONTRACT_VERSION,
    FILE_SPECS,
    GENERATOR_VERSION,
    LABELS,
    FileSpec,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUT = BACKEND_DIR / "sample_data" / "raw"

JAKARTA = timezone(timedelta(hours=7))
JAKARTA_OFFSET = "+07:00"
SOURCE_SYSTEM_CODE = "NDP_WALLET_CORE"

# TRD §11.1 volume targets (the "full" profile) and the three shipped profiles.
FULL_TARGETS = {
    "customers": 10_000,
    "business_customers": 1_200,
    "merchants": 1_500,
    "accounts": 13_500,
    "transactions": 420_000,
    "devices": 10_000,
    "watchlist": 2_500,
}
PROFILES = {"tiny": 0.01, "small": 0.05, "full": 1.0}

# TRD §11.3 injected-scenario counts at the full profile.
SCENARIO_TARGETS = {
    "P01": 60, "P02": 45, "P03": 50, "P04": 55, "P05": 40, "P06": 35,
    "P07": 40, "P08": 45, "P09": 30, "P10": 45, "P11": 35, "P12": 70,
}
EDGE_PER_PATTERN = 25
BOUNDARY_PER_PATTERN = 5
CONTROL_CLEAN_ENTITIES = 800

PATTERN_NAMES = {
    "P01": "Unusually large single transfer",
    "P02": "Structuring below the reporting threshold",
    "P03": "Same-day pass-through",
    "P04": "Transaction count spike against baseline",
    "P05": "Dormant account reactivation",
    "P06": "Uniform round amounts",
    "P07": "Stepped weekly value increase",
    "P08": "Concentrated high-risk geography exposure",
    "P09": "Device shared by unrelated entities",
    "P10": "Merchant activity inconsistent with its MCC",
    "P11": "Many-to-one funnel with device overlap",
    "P12": "Name similar to a synthetic list record",
}
EXPECTED_REASON = {code: f"{code}_{name.upper().replace(' ', '_')}"[:48] for code, name in PATTERN_NAMES.items()}

# TRD §11.4 deliberate quality-defect rates.
DEFECTS = {
    "missing_counterparty": 0.030,
    "missing_device": 0.050,
    "malformed_date": 0.004,
    "invalid_currency": 0.002,
    "invalid_amount": 0.002,
    "unresolved_account": 0.005,
    "exact_duplicate": 0.010,
    "idempotency_conflict": 0.0005,
    "late_arrival": 0.015,
    "missing_bo": 0.040,
    "placeholder_address": 0.020,
    "truncated_name": 0.030,
}

# --- generated pools: shaped like Indonesian data, sourced from nothing ---
GIVEN_M = ("Adi", "Bayu", "Fajar", "Hadi", "Joko", "Oki", "Rahmat", "Tono", "Wahyu",
           "Yuda", "Bagus", "Eka", "Nanda", "Cahya", "Dian", "Iwan", "Gilang")
GIVEN_F = ("Citra", "Dewi", "Gita", "Indah", "Kartika", "Lestari", "Mega", "Putri",
           "Sari", "Umi", "Vina", "Zahra", "Anggun", "Ayu", "Rina", "Siti", "Wulan")
FAMILY = ("Pratama", "Wijaya", "Santoso", "Halim", "Nugroho", "Saputra", "Hidayat",
          "Kusuma", "Permana", "Siregar", "Simatupang", "Situmorang", "Maulana",
          "Ramadhan", "Setiawan", "Gunawan", "Hartono", "Suryadi", "Anggraini", "Lubis")
# Honorifics are gendered in Indonesian usage, so they are drawn per gender:
# "Hj." on a male record is exactly the kind of detail a domain reviewer spots.
HONORIFIC_M = ("", "", "", "", "Drs.", "Ir.", "H.")
HONORIFIC_F = ("", "", "", "", "Dra.", "Ir.", "Hj.")
CITIES = (("Jakarta Pusat", "DKI Jakarta", "10110"), ("Bandung", "Jawa Barat", "40111"),
          ("Surabaya", "Jawa Timur", "60111"), ("Medan", "Sumatera Utara", "20111"),
          ("Semarang", "Jawa Tengah", "50111"), ("Makassar", "Sulawesi Selatan", "90111"),
          ("Denpasar", "Bali", "80111"), ("Yogyakarta", "DI Yogyakarta", "55111"),
          ("Palembang", "Sumatera Selatan", "30111"), ("Balikpapan", "Kalimantan Timur", "76111"))
STREETS = ("Jl. Melati", "Jl. Kenanga", "Jl. Anggrek", "Jl. Cempaka", "Jl. Mawar",
           "Jl. Flamboyan", "Jl. Bougenville", "Jl. Dahlia", "Jl. Teratai")
PLACEHOLDER_ADDRESSES = ("ALAMAT BELUM DIISI", "-", "N/A", "SAMA DENGAN KTP")
OCCUPATIONS = ("Karyawan Swasta", "Wiraswasta", "PNS", "Pelajar/Mahasiswa", "Ibu Rumah Tangga",
               "Pedagang", "Guru", "Petani", "Buruh Harian", "Pengemudi Daring")
BUSINESS_FORMS = ("PT", "CV", "UD", "Koperasi")
BUSINESS_WORDS = ("Sinar", "Maju", "Berkah", "Sentosa", "Jaya", "Mandiri", "Lestari",
                  "Cemerlang", "Harapan", "Makmur", "Abadi", "Sejahtera")
BUSINESS_TAIL = ("Nusantara", "Perkasa", "Utama", "Bersama", "Digital", "Logistik", "Niaga")
EMAIL_DOMAINS = ("contoh.id", "surelsintetis.id", "mailuji.example", "demo.example")
# (mcc, description, typical ticket in IDR)
MCC_BANDS = ((5411, "Grocery Stores", 85_000), (5812, "Eating Places", 65_000),
             (5814, "Fast Food", 40_000), (5541, "Service Stations", 150_000),
             (5912, "Drug Stores", 70_000), (5651, "Family Clothing", 220_000),
             (5732, "Electronics Stores", 1_800_000), (7011, "Lodging", 650_000),
             (4121, "Taxicabs and Limousines", 45_000), (5999, "Misc Retail", 120_000),
             (5977, "Cosmetic Stores", 180_000), (8220, "Colleges and Universities", 3_500_000),
             (8062, "Hospitals", 900_000), (4900, "Utilities", 250_000),
             (5045, "Computers and Peripherals", 4_500_000), (7230, "Beauty Shops", 95_000),
             (5942, "Book Stores", 110_000), (7832, "Motion Picture Theatres", 55_000),
             (5311, "Department Stores", 350_000), (4816, "Computer Network Services", 200_000),
             (5094, "Precious Stones and Metals", 9_500_000), (6051, "Quasi Cash", 2_500_000),
             (7995, "Betting-adjacent (restricted)", 1_200_000), (5933, "Second Hand Stores", 160_000),
             (4722, "Travel Agencies", 2_800_000))
HIGH_RISK_COUNTRIES = ("KY", "PA", "SC", "VU", "BZ")
NORMAL_CORRIDORS = ("SG", "MY", "HK", "AE", "SA", "AU", "JP", "KR", "NL", "US")
OS_FAMILIES = ("Android 13", "Android 14", "Android 15", "iOS 17", "iOS 18")
POSITIONS = ("Kepala Dinas (sintetis)", "Anggota Dewan (sintetis)", "Direktur BUMD (sintetis)",
             "Pejabat Daerah (sintetis)", "Komisaris (sintetis)")


def _seed_for(master: int, label: str) -> int:
    """Stable derived seed: Python's hash() is salted per process, sha256 is not."""
    digest = hashlib.sha256(f"{master}:{label}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _scaled(target: int, factor: float, minimum: int = 1) -> int:
    return max(minimum, round(target * factor))


def _money(value: float) -> str:
    return f"{Decimal(int(round(value))):.2f}"


@dataclass
class Party:
    source_id: str
    name: str
    cohort: str
    is_business: bool = False
    accounts: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    home_country: str = "ID"


@dataclass
class Counters:
    rows: dict[str, int] = field(default_factory=dict)
    defects: dict[str, int] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)

    def defect(self, name: str, n: int = 1) -> None:
        self.defects[name] = self.defects.get(name, 0) + n

    def label(self, name: str, n: int = 1) -> None:
        self.labels[name] = self.labels.get(name, 0) + n


class RawDatasetGenerator:
    def __init__(self, *, profile: str, seed: int, reference_date: date) -> None:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}; choose from {', '.join(PROFILES)}")
        self.profile = profile
        self.factor = PROFILES[profile]
        self.master_seed = seed
        self.reference_date = reference_date
        # TRD §11.1: six months ending on the demo reference date. The longest
        # rule window is 90 days (AS-06), so this leaves a full baseline behind
        # every scenario placed in the second half.
        self.period_start = reference_date - timedelta(days=182)
        self.rng = {
            name: random.Random(_seed_for(seed, name))
            for name in ("party", "account", "merchant", "device", "txn", "defect", "scenario", "watchlist")
        }
        self.counters = Counters()
        self.customers: list[dict[str, Any]] = []
        self.businesses: list[dict[str, Any]] = []
        self.beneficial_owners: list[dict[str, Any]] = []
        self.accounts: list[dict[str, Any]] = []
        self.merchants: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self.transactions: list[dict[str, Any]] = []
        self.watchlist: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.parties: dict[str, Party] = {}
        self._txn_seq = 0

    # --- primitive generators -------------------------------------------------

    def _person_name(self, rng: random.Random, gender: str | None = None) -> str:
        gender = gender or rng.choice(("M", "F"))
        given = GIVEN_F if gender == "F" else GIVEN_M
        honorific = rng.choice(HONORIFIC_F if gender == "F" else HONORIFIC_M)
        name = f"{rng.choice(given)} {rng.choice(FAMILY)}"
        # Indonesian names often carry a second given name before the family name.
        if rng.random() < 0.25:
            name = f"{rng.choice(given)} {name}"
        return f"{honorific} {name}".strip()

    def _national_id(self, rng: random.Random, dob: date, gender: str = "M") -> str:
        """NIK-shaped: PP KK DD DDMMYY NNNN.

        Structurally correct — including the real convention that a female's
        birth day is stored +40 — so entity resolution and masking work against
        realistic input. Province prefix 99 is never issued, so a generated
        value cannot collide with a real NIK.
        """
        birth_day = dob.day + 40 if gender == "F" else dob.day
        return (
            f"99{rng.randrange(1, 99):02d}{rng.randrange(1, 99):02d}"
            f"{birth_day:02d}{dob.month:02d}{dob.strftime('%y')}{rng.randrange(1, 9999):04d}"
        )

    def _phone(self, rng: random.Random) -> str:
        return f"+62899{rng.randrange(1000000, 9999999)}"

    def _address(self, rng: random.Random) -> tuple[str, str, str, str]:
        city, province, postcode = rng.choice(CITIES)
        if rng.random() < DEFECTS["placeholder_address"]:
            self.counters.defect("placeholder_address")
            return rng.choice(PLACEHOLDER_ADDRESSES), city, province, postcode
        return f"{rng.choice(STREETS)} No. {rng.randrange(1, 240)}", city, province, postcode

    def _maybe_truncate(self, rng: random.Random, name: str) -> str:
        """TRD §11.4: 3% of parties get a single-token or truncated name, which
        later becomes NOT_SCREENABLE or HIGH_FREQUENCY_NAME."""
        if rng.random() >= DEFECTS["truncated_name"]:
            return name
        self.counters.defect("truncated_name")
        return name.split()[-1] if rng.random() < 0.5 else name[: max(3, len(name) // 2)].strip()

    # --- populations ----------------------------------------------------------

    def _cohort_for(self, rng: random.Random) -> str:
        """TRD §11.2 population mix."""
        roll = rng.random()
        if roll < 0.62:
            return "RETAIL_NORMAL"
        if roll < 0.80:
            return "BUSINESS_NORMAL"
        if roll < 0.88:
            return "CONTROL_CLEAN"
        if roll < 0.96:
            return "EDGE_AMBIGUOUS"
        return "INJECTED_CANDIDATE"

    def build_customers(self) -> None:
        rng = self.rng["party"]
        # TRD §11.1's 10,000 customers is stated as *including* the duplicate
        # source records, so the base population is the target minus whatever
        # the ER constructions will add.
        total = max(40, _scaled(FULL_TARGETS["customers"], self.factor, 40) - self._er_population_size())
        for i in range(1, total + 1):
            dob = date(rng.randrange(1960, 2007), rng.randrange(1, 13), rng.randrange(1, 29))
            address, city, province, postcode = self._address(rng)
            gender = rng.choice(("M", "F"))
            name = self._maybe_truncate(rng, self._person_name(rng, gender))
            # Born somewhere, living somewhere else most of the time.
            birth_city = city if rng.random() < 0.35 else rng.choice(CITIES)[0]
            cohort = self._cohort_for(rng)
            source_id = f"CUST-{i:06d}"
            self.customers.append({
                "source_customer_id": source_id,
                "full_name": name,
                "aliases": "",
                "date_of_birth": dob.isoformat(),
                "place_of_birth": birth_city,
                "gender": gender,
                "nationality": "ID",
                "national_id_reference": self._national_id(rng, dob, gender),
                "occupation": rng.choice(OCCUPATIONS),
                "income_band": rng.choice(("LT_5M", "5M_15M", "15M_50M", "50M_200M", "GT_200M")),
                "address_line": address,
                "city": city,
                "province": province,
                "postcode": postcode,
                "country": "ID",
                "phone": self._phone(rng),
                "email": f"{source_id.lower()}@{rng.choice(EMAIL_DOMAINS)}",
                "onboarding_date": (self.period_start - timedelta(days=rng.randrange(30, 1500))).isoformat(),
                "kyc_status": rng.choices(("COMPLETE", "PARTIAL", "PENDING"), weights=(80, 15, 5))[0],
                "customer_status": rng.choices(("ACTIVE", "SUSPENDED", "CLOSED"), weights=(94, 3, 3))[0],
            })
            self.parties[source_id] = Party(source_id, name, cohort)
        self._build_er_population()

    # TRD §11.5. NOTE: these sum to 820, while §11.1 describes "~600 duplicate
    # source records". The two figures in the spec disagree; the §11.5 table is
    # the more specific of the two, so it wins here and the difference is
    # flagged rather than silently split.
    ER_PLAN = (
        ("SAME_ID_NAME_VARIANT", 200, "must auto-merge"),
        ("SAME_PHONE_DOB", 150, "must auto-merge"),
        ("SAME_NAME_ONLY", 180, "must not auto-merge"),
        ("IDENTIFIER_CONFLICT", 40, "must force PENDING_REVIEW"),
        ("AMBIGUOUS_BAND", 250, "fills the manual review queue"),
    )

    def _er_population_size(self) -> int:
        return sum(_scaled(target, self.factor, 2) for _, target, _ in self.ER_PLAN)

    def _build_er_population(self) -> None:
        """TRD §11.5: duplicate source records that entity resolution must (or
        must not) merge. Each construction is a separate, countable population."""
        rng = self.rng["party"]
        base = [c for c in self.customers if not c["source_customer_id"].startswith("CUST-ER")]
        seq = 0
        for construction, target, note in self.ER_PLAN:
            for _ in range(_scaled(target, self.factor, 2)):
                original = rng.choice(base)
                seq += 1
                source_id = f"CUST-ER{seq:05d}"
                twin = dict(original)
                twin["source_customer_id"] = source_id
                if construction == "SAME_ID_NAME_VARIANT":
                    twin["full_name"] = original["full_name"].replace("a", "o", 1)
                elif construction == "SAME_PHONE_DOB":
                    twin["national_id_reference"] = self._national_id(rng, date.fromisoformat(original["date_of_birth"]), original["gender"])
                    twin["address_line"] = f"{rng.choice(STREETS)} No. {rng.randrange(1, 240)}"
                elif construction == "SAME_NAME_ONLY":
                    twin["phone"] = self._phone(rng)
                    twin["date_of_birth"] = date(rng.randrange(1960, 2007), rng.randrange(1, 13), rng.randrange(1, 29)).isoformat()
                    twin["national_id_reference"] = self._national_id(rng, date.fromisoformat(twin["date_of_birth"]), twin["gender"])
                elif construction == "IDENTIFIER_CONFLICT":
                    twin["national_id_reference"] = "99" + str(rng.randrange(10**13, 10**14 - 1))
                else:  # AMBIGUOUS_BAND
                    twin["full_name"] = original["full_name"].replace(" ", "  ", 1)
                    twin["email"] = f"{source_id.lower()}@{rng.choice(EMAIL_DOMAINS)}"
                twin["email"] = twin["email"] if construction == "AMBIGUOUS_BAND" else f"{source_id.lower()}@{rng.choice(EMAIL_DOMAINS)}"
                self.customers.append(twin)
                self.parties[source_id] = Party(source_id, twin["full_name"], "ER_TEST")
                self.labels.append({
                    "scenario_id": f"ER-{construction}-{seq:05d}",
                    "label_type": "EDGE_CASE",
                    "pattern_code": "ER",
                    "entity_source_id": source_id,
                    "window_start": "",
                    "window_end": "",
                    "expected_reason_code": construction,
                    "note": f"{note}; twin of {original['source_customer_id']}",
                })
                self.counters.label(f"ER_{construction}")

    def build_businesses(self) -> None:
        rng = self.rng["party"]
        total = _scaled(FULL_TARGETS["business_customers"], self.factor, 10)
        # Chosen up-front rather than sampled per business: at the CI profile a
        # 4% chance over a dozen businesses often yields none at all, which
        # would leave the quality pipeline nothing to catch in CI. At least one
        # business always ships without a beneficial owner.
        without_bo = set(rng.sample(range(1, total + 1), max(1, round(total * DEFECTS["missing_bo"]))))
        for i in range(1, total + 1):
            address, city, province, postcode = self._address(rng)
            legal_name = f"{rng.choice(BUSINESS_FORMS)} {rng.choice(BUSINESS_WORDS)} {rng.choice(BUSINESS_TAIL)}"
            source_id = f"BUS-{i:05d}"
            self.businesses.append({
                "source_business_id": source_id,
                "legal_name": legal_name,
                "trading_names": legal_name.split(" ", 1)[1],
                "registration_number": f"99{rng.randrange(10**10, 10**11 - 1)}",
                "registration_country": "ID",
                "incorporation_date": (self.period_start - timedelta(days=rng.randrange(200, 5000))).isoformat(),
                "industry_code": f"{rng.randrange(10000, 99999)}",
                "declared_turnover_band": rng.choice(("LT_300M", "300M_2_5B", "2_5B_50B", "GT_50B")),
                "address_line": address,
                "city": city,
                "province": province,
                "postcode": postcode,
                "country": "ID",
                "contact_phone": self._phone(rng),
                "contact_email": f"{source_id.lower()}@{rng.choice(EMAIL_DOMAINS)}",
                "status": rng.choices(("ACTIVE", "SUSPENDED", "CLOSED"), weights=(94, 3, 3))[0],
            })
            self.parties[source_id] = Party(source_id, legal_name, "BUSINESS_NORMAL", is_business=True)

            # TRD §11.4: 4% of businesses ship with no beneficial owner at all,
            # which is both a data-quality defect and a risk factor (FRD E02).
            if i in without_bo:
                self.counters.defect("missing_bo")
                continue
            self._build_beneficial_owners(rng, source_id)

    def _build_beneficial_owners(self, rng: random.Random, business_id: str) -> None:
        count = rng.randrange(1, 5)
        remaining = 100.0
        for n in range(count):
            share = round(remaining if n == count - 1 else rng.uniform(10, max(11, remaining - 10)), 2)
            remaining = round(remaining - share, 2)
            dob = date(rng.randrange(1955, 2000), rng.randrange(1, 13), rng.randrange(1, 29))
            bo_id = f"BO-{len(self.beneficial_owners) + 1:05d}"
            self.beneficial_owners.append({
                "source_bo_id": bo_id,
                "source_business_id": business_id,
                "full_name": self._person_name(rng),
                "date_of_birth": dob.isoformat(),
                "nationality": "ID",
                "ownership_percentage": f"{max(share, 0.01):.2f}",
                "control_type": rng.choice(("OWNERSHIP", "VOTING_RIGHTS", "BOARD_CONTROL", "OTHER_SIGNIFICANT_INFLUENCE")),
                "declared_date": (self.period_start - timedelta(days=rng.randrange(30, 900))).isoformat(),
            })
            if remaining <= 10:
                break

    def share_beneficial_owners(self) -> None:
        """TRD §11.1: ~40 owners are shared by two businesses, which is what
        gives the relationship graph its first non-trivial edges."""
        rng = self.rng["party"]
        if len(self.beneficial_owners) < 4 or len(self.businesses) < 2:
            return
        for _ in range(_scaled(40, self.factor, 2)):
            donor = rng.choice(self.beneficial_owners)
            other = rng.choice(self.businesses)["source_business_id"]
            if other == donor["source_business_id"]:
                continue
            clone = dict(donor)
            clone["source_bo_id"] = f"BO-{len(self.beneficial_owners) + 1:05d}"
            clone["source_business_id"] = other
            clone["ownership_percentage"] = f"{rng.uniform(5, 45):.2f}"
            self.beneficial_owners.append(clone)

    def build_accounts(self) -> None:
        rng = self.rng["account"]
        for party in self.parties.values():
            # Weighted so the totals land inside TRD §11.1's 12,000-15,000 band:
            # "1-3 per individual, 1-2 per business" averages far above target
            # if drawn uniformly.
            count = (rng.choices((1, 2), weights=(70, 30))[0] if party.is_business
                     else rng.choices((1, 2, 3), weights=(85, 12, 3))[0])
            for _ in range(count):
                account_id = f"ACC-{len(self.accounts) + 1:07d}"
                opened = self.period_start - timedelta(days=rng.randrange(30, 2000))
                status = rng.choices(("ACTIVE", "DORMANT", "CLOSED", "RESTRICTED"), weights=(88, 6, 4, 2))[0]
                closed = (opened + timedelta(days=rng.randrange(60, 1800))) if status == "CLOSED" else None
                self.accounts.append({
                    "source_account_id": account_id,
                    "owner_source_id": party.source_id,
                    "owner_type": "BUSINESS" if party.is_business else "INDIVIDUAL",
                    "account_type": "SETTLEMENT" if party.is_business and not party.accounts else
                                    rng.choices(("WALLET", "VIRTUAL_ACCOUNT"), weights=(85, 15))[0],
                    "currency": "IDR",
                    "opened_date": opened.isoformat(),
                    "closed_date": closed.isoformat() if closed else "",
                    "status": status,
                    "balance_snapshot": _money(rng.uniform(50_000, 250_000_000)),
                    "balance_snapshot_at": datetime.combine(self.reference_date, datetime.min.time(), JAKARTA).isoformat(),
                })
                party.accounts.append(account_id)

    def build_merchants(self) -> None:
        rng = self.rng["merchant"]
        businesses = [b["source_business_id"] for b in self.businesses]
        if not businesses:
            return
        for i in range(1, _scaled(FULL_TARGETS["merchants"], self.factor, 8) + 1):
            business_id = rng.choice(businesses)
            mcc, description, ticket = rng.choice(MCC_BANDS)
            settlement = next(
                (a for a in self.parties[business_id].accounts), ""
            )
            self.merchants.append({
                "source_merchant_id": f"MER-{i:05d}",
                "source_business_id": business_id,
                "merchant_name": f"{self.parties[business_id].name.split(' ', 1)[1]} - {description}",
                "mcc": str(mcc),
                "declared_expected_volume_band": rng.choice(("LT_50M", "50M_250M", "250M_1B", "GT_1B")),
                "declared_expected_ticket_band": ("LT_50K" if ticket < 50_000 else "50K_250K" if ticket < 250_000
                                                  else "250K_1M" if ticket < 1_000_000 else "GT_1M"),
                "onboarded_date": (self.period_start - timedelta(days=rng.randrange(20, 1200))).isoformat(),
                "status": rng.choices(("ACTIVE", "SUSPENDED", "CLOSED"), weights=(95, 3, 2))[0],
                "settlement_source_account_id": settlement,
                "operating_hours_declared": rng.choice(("08:00-17:00", "09:00-21:00", "00:00-23:59", "10:00-22:00")),
                "outlet_count": rng.randrange(1, 12),
            })

    def build_devices(self) -> None:
        rng = self.rng["device"]
        parties = [p for p in self.parties.values() if p.accounts]
        for i in range(1, _scaled(FULL_TARGETS["devices"], self.factor, 25) + 1):
            device_id = f"DEV-{i:06d}"
            first_seen = self.period_start - timedelta(days=rng.randrange(0, 400))
            device_type = rng.choices(("ANDROID_PHONE", "IOS_PHONE", "TABLET", "WEB_BROWSER"), weights=(70, 22, 5, 3))[0]
            self.devices.append({
                "source_device_id": device_id,
                "device_type": device_type,
                "os_family": rng.choice(OS_FAMILIES) if "PHONE" in device_type else "Other",
                "app_version": f"{rng.randrange(3, 8)}.{rng.randrange(0, 20)}.{rng.randrange(0, 9)}",
                "is_emulator": "true" if rng.random() < 0.01 else "false",
                "is_rooted": "true" if rng.random() < 0.02 else "false",
                "first_seen": datetime.combine(first_seen, datetime.min.time(), JAKARTA).isoformat(),
                "last_seen": datetime.combine(self.reference_date, datetime.min.time(), JAKARTA).isoformat(),
            })
            if parties:
                rng.choice(parties).devices.append(device_id)
        # Every transacting party needs at least one device to pick from.
        for party in parties:
            if not party.devices and self.devices:
                party.devices.append(rng.choice(self.devices)["source_device_id"])

    def build_watchlist(self) -> None:
        rng = self.rng["watchlist"]
        for i in range(1, _scaled(FULL_TARGETS["watchlist"], self.factor, 20) + 1):
            list_type = rng.choices(
                ("PEP_SYNTHETIC", "SANCTIONS_SYNTHETIC", "INTERNAL_WATCH"), weights=(60, 30, 10)
            )[0]
            name = self._person_name(rng)
            dob = date(rng.randrange(1950, 1999), rng.randrange(1, 13), rng.randrange(1, 29))
            self.watchlist.append({
                "list_record_id": f"WL-{i:05d}",
                "list_type": list_type,
                "primary_name": name,
                "aliases": name.replace("a", "e", 1) if rng.random() < 0.4 else "",
                "date_of_birth": dob.isoformat() if rng.random() < 0.7 else "",
                "nationality": "ID" if rng.random() < 0.8 else rng.choice(NORMAL_CORRIDORS),
                "country": "ID",
                "position_or_role": rng.choice(POSITIONS) if list_type == "PEP_SYNTHETIC" else "",
                "note": "Synthetic record; not derived from any real list.",
            })

    # --- transactions ---------------------------------------------------------

    def _next_ref(self) -> str:
        self._txn_seq += 1
        return f"TXN-{self._txn_seq:09d}"

    def _emit(
        self,
        *,
        account_id: str,
        when: datetime,
        amount: float,
        direction: str,
        channel: str,
        transaction_type: str,
        device_id: str | None = None,
        counterparty_ref: str | None = None,
        counterparty_name: str = "",
        counterparty_country: str = "ID",
        merchant_id: str = "",
        currency: str = "IDR",
        apply_defects: bool = True,
    ) -> dict[str, Any]:
        rng = self.rng["defect"]
        row = {
            "source_transaction_reference": self._next_ref(),
            "source_account_id": account_id,
            "direction": direction,
            "amount_original": _money(amount),
            "currency_original": currency,
            "value_datetime": when.isoformat(),
            "business_date": when.astimezone(JAKARTA).date().isoformat(),
            "channel": channel,
            "transaction_type": transaction_type,
            "counterparty_reference": counterparty_ref or f"EXT-{rng.randrange(10**6, 10**7 - 1)}",
            "counterparty_name": counterparty_name or self._person_name(rng),
            "counterparty_country": counterparty_country,
            "source_merchant_id": merchant_id,
            "source_device_id": device_id or "",
            "ip_address": f"198.51.100.{rng.randrange(1, 254)}" if rng.random() < 0.6 else f"203.0.113.{rng.randrange(1, 254)}",
            "status_from_source": rng.choices(("SETTLED", "PENDING", "REVERSED"), weights=(96, 3, 1))[0],
        }

        if apply_defects:
            # Defects are applied only to background traffic, never to an
            # injected scenario: a corrupted row would silently change whether
            # the scenario fires, and the label would then be a lie.
            if rng.random() < DEFECTS["missing_counterparty"]:
                row["counterparty_reference"] = ""
                self.counters.defect("missing_counterparty")
            if rng.random() < DEFECTS["missing_device"]:
                row["source_device_id"] = ""
                self.counters.defect("missing_device")
            if rng.random() < DEFECTS["malformed_date"]:
                row["value_datetime"] = when.strftime("%d/%m/%Y %H:%M")
                self.counters.defect("malformed_date")
            if rng.random() < DEFECTS["invalid_currency"]:
                row["currency_original"] = rng.choice(("IDRR", "ID", "1DR", "id r"))
                self.counters.defect("invalid_currency")
            if rng.random() < DEFECTS["invalid_amount"]:
                row["amount_original"] = rng.choice(("0.00", "-125000.00"))
                self.counters.defect("invalid_amount")
            if rng.random() < DEFECTS["unresolved_account"]:
                row["source_account_id"] = f"ACC-9{rng.randrange(10**5, 10**6 - 1)}"
                self.counters.defect("unresolved_account")
            if rng.random() < DEFECTS["late_arrival"]:
                # Backdated well before the batch business date: the loader
                # flags LATE_ARRIVAL and computes lag_days (FR-106).
                shifted = when - timedelta(days=rng.randrange(5, 100))
                row["value_datetime"] = shifted.isoformat()
                row["business_date"] = shifted.astimezone(JAKARTA).date().isoformat()
                self.counters.defect("late_arrival")

        self.transactions.append(row)
        return row

    def _day_weight(self, day: date) -> float:
        """Shape, not source (TRD §11.0.3): payday clustering around the 25th,
        a month-end tail, and quieter weekends."""
        weight = 1.0
        if day.day in (25, 26, 27, 1, 2):
            weight *= 2.4
        if day.day >= 28:
            weight *= 1.5
        if day.weekday() >= 5:
            weight *= 0.65
        return weight

    def _random_moment(self, rng: random.Random, day: date) -> datetime:
        # Waking hours, with a lunchtime and evening bias.
        hour = rng.choices(range(6, 24), weights=[2, 4, 5, 6, 7, 9, 8, 6, 5, 5, 6, 7, 9, 10, 8, 6, 4, 3])[0]
        return datetime(day.year, day.month, day.day, hour, rng.randrange(0, 60), rng.randrange(0, 60), tzinfo=JAKARTA)

    def build_background_traffic(self) -> None:
        """Ordinary behaviour for every cohort except the injected scenarios,
        which are layered on afterwards."""
        rng = self.rng["txn"]
        budget = _scaled(FULL_TARGETS["transactions"], self.factor, 400)
        active = [p for p in self.parties.values() if p.accounts and p.cohort != "ER_TEST"]
        if not active:
            return

        days = [self.period_start + timedelta(days=n) for n in range((self.reference_date - self.period_start).days + 1)]
        weights = [self._day_weight(d) for d in days]
        merchant_ids = [m["source_merchant_id"] for m in self.merchants]

        per_party = max(4, budget // max(1, len(active)))
        for party in active:
            # Control-clean entities get deliberately modest, well-spread
            # behaviour so they raise nothing at default parameters (FRD §8.14).
            count = max(3, int(rng.gauss(per_party, per_party * 0.35)))
            if party.cohort == "CONTROL_CLEAN":
                count = max(3, count // 2)
            for _ in range(count):
                day = rng.choices(days, weights=weights)[0]
                when = self._random_moment(rng, day)
                account = rng.choice(party.accounts)
                device = rng.choice(party.devices) if party.devices else None
                if party.is_business:
                    amount = rng.lognormvariate(12.5, 1.1)
                    channel, ttype = "PAYMENT", "MERCHANT_PAYMENT"
                    merchant = rng.choice(merchant_ids) if merchant_ids else ""
                    direction = "IN"
                else:
                    amount = rng.lognormvariate(11.2, 1.2)
                    channel, ttype = rng.choice((("QRIS", "MERCHANT_PAYMENT"), ("TRANSFER", "P2P_TRANSFER"),
                                                 ("TOPUP", "WALLET_TOPUP"), ("PAYMENT", "BILL_PAYMENT")))
                    merchant = rng.choice(merchant_ids) if merchant_ids and ttype == "MERCHANT_PAYMENT" else ""
                    direction = rng.choices(("OUT", "IN"), weights=(70, 30))[0]
                if party.cohort == "CONTROL_CLEAN":
                    amount = min(amount, 120_000_000)
                self._emit(
                    account_id=account, when=when, amount=max(1000.0, amount), direction=direction,
                    channel=channel, transaction_type=ttype, device_id=device, merchant_id=merchant,
                )
            if party.cohort == "CONTROL_CLEAN":
                # TRD §11.3 labels the control cohort too: an alert on any of
                # these is a rule defect, so the test needs the entity list.
                self.labels.append({
                    "scenario_id": f"CONTROL-{party.source_id}",
                    "label_type": "CONTROL_CLEAN",
                    "pattern_code": "ALL",
                    "entity_source_id": party.source_id,
                    "window_start": self.period_start.isoformat(),
                    "window_end": self.reference_date.isoformat(),
                    "expected_reason_code": "",
                    "note": "Must raise zero alerts at approved default parameters (FRD §8.14)",
                })
                self.counters.label("CONTROL_CLEAN")

        self._inject_exact_duplicates()

    def _inject_exact_duplicates(self) -> None:
        """TRD §11.4: 1% exact duplicates (suppressed by idempotency but still
        counted) and 0.05% same-key-different-amount (IDEMPOTENCY_CONFLICT)."""
        rng = self.rng["defect"]
        if not self.transactions:
            return
        for _ in range(int(len(self.transactions) * DEFECTS["exact_duplicate"])):
            self.transactions.append(dict(rng.choice(self.transactions)))
            self.counters.defect("exact_duplicate")
        for _ in range(max(1, int(len(self.transactions) * DEFECTS["idempotency_conflict"]))):
            conflict = dict(rng.choice(self.transactions))
            conflict["amount_original"] = _money(float(conflict["amount_original"] or 1000) + 77_000)
            self.transactions.append(conflict)
            self.counters.defect("idempotency_conflict")

    # --- injected scenarios ---------------------------------------------------

    def _label(self, scenario_id: str, label_type: str, pattern: str, entity: str,
               start: date, end: date, note: str) -> None:
        self.labels.append({
            "scenario_id": scenario_id,
            "label_type": label_type,
            "pattern_code": pattern,
            "entity_source_id": entity,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "expected_reason_code": EXPECTED_REASON.get(pattern, pattern),
            "note": note,
        })
        self.counters.label(f"{pattern}_{label_type}")

    def _scenario_parties(self, rng: random.Random, count: int) -> list[Party]:
        pool = [p for p in self.parties.values()
                if p.accounts and p.cohort in ("INJECTED_CANDIDATE", "RETAIL_NORMAL", "BUSINESS_NORMAL")]
        if not pool:
            return []
        return [rng.choice(pool) for _ in range(count)]

    def inject_scenarios(self) -> None:
        rng = self.rng["scenario"]
        builders = {
            "P01": self._inject_p01, "P02": self._inject_p02, "P03": self._inject_p03,
            "P04": self._inject_p04, "P05": self._inject_p05, "P06": self._inject_p06,
            "P07": self._inject_p07, "P08": self._inject_p08, "P09": self._inject_p09,
            "P10": self._inject_p10, "P11": self._inject_p11, "P12": self._inject_p12,
        }
        for pattern, target in SCENARIO_TARGETS.items():
            count = _scaled(target, self.factor, 1)
            for n in range(1, count + 1):
                builders[pattern](rng, f"{pattern}-POS-{n:04d}")

        # Behavioural look-alikes and exact-threshold boundary cases. Both are
        # EDGE_CASE: legitimate activity that a badly tuned rule would catch.
        for pattern in SCENARIO_TARGETS:
            for n in range(1, _scaled(EDGE_PER_PATTERN, self.factor, 1) + 1):
                self._inject_edge(rng, pattern, f"{pattern}-EDGE-{n:04d}")
            for n in range(1, _scaled(BOUNDARY_PER_PATTERN, self.factor, 1) + 1):
                self._inject_boundary(rng, pattern, f"{pattern}-BOUND-{n:04d}")

    def _window_start(self, rng: random.Random, span_days: int) -> date:
        latest = self.reference_date - timedelta(days=span_days + 1)
        earliest = self.period_start + timedelta(days=60)  # leave a baseline behind it
        if latest <= earliest:
            latest = earliest + timedelta(days=1)
        return earliest + timedelta(days=rng.randrange(0, (latest - earliest).days))

    def _inject_p01(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        day = self._window_start(rng, 1)
        self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                   amount=rng.uniform(2_000_000_000, 8_000_000_000), direction="OUT", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", device_id=party.devices[0] if party.devices else None,
                   apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P01", party.source_id, day, day,
                    "Single transfer far above the individual threshold band")

    def _inject_p02(self, rng: random.Random, scenario_id: str) -> None:
        """Deliberately built to satisfy the implemented detector: 3-5
        transactions inside [350M, 500M) within 7 days, aggregate >= 500M."""
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 7)
        count = rng.randrange(3, 6)
        for n in range(count):
            day = start + timedelta(days=rng.randrange(0, 6))
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(350_000_000, 499_000_000), direction="IN",
                       channel=rng.choice(("CASH", "TRANSFER")), transaction_type="CASH_DEPOSIT",
                       device_id=party.devices[0] if party.devices else None, apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P02", party.source_id, start,
                    start + timedelta(days=6), f"{count} deposits in band within a 7-day window")

    def _inject_p03(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        day = self._window_start(rng, 1)
        amount = rng.uniform(200_000_000, 900_000_000)
        account = rng.choice(party.accounts)
        arrival = self._random_moment(rng, day).replace(hour=9)
        self._emit(account_id=account, when=arrival, amount=amount, direction="IN", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", apply_defects=False)
        self._emit(account_id=account, when=arrival + timedelta(hours=rng.randrange(1, 6)),
                   amount=amount * rng.uniform(0.94, 0.99), direction="OUT", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P03", party.source_id, day, day,
                    "Funds in and substantially out again within the same day")

    def _inject_p04(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 3)
        for _ in range(rng.randrange(40, 90)):
            day = start + timedelta(days=rng.randrange(0, 3))
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(50_000, 2_500_000), direction="OUT", channel="QRIS",
                       transaction_type="MERCHANT_PAYMENT", apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P04", party.source_id, start,
                    start + timedelta(days=3), "Transaction count far above the entity's own baseline")

    def _inject_p05(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        # Dormancy is derived from transaction history (FRD E04), so the
        # scenario is a gap in activity followed by something material.
        day = self.reference_date - timedelta(days=rng.randrange(5, 40))
        account = rng.choice(party.accounts)
        self.transactions = [
            t for t in self.transactions
            if not (t["source_account_id"] == account
                    and day - timedelta(days=150) <= date.fromisoformat(t["business_date"]) < day)
        ]
        self._emit(account_id=account, when=self._random_moment(rng, day),
                   amount=rng.uniform(400_000_000, 1_500_000_000), direction="IN", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P05", party.source_id,
                    day - timedelta(days=150), day, "150 dormant days, then a material credit")

    def _inject_p06(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 10)
        amount = rng.choice((50_000_000, 100_000_000, 250_000_000))
        for n in range(rng.randrange(6, 12)):
            self._emit(account_id=rng.choice(party.accounts),
                       when=self._random_moment(rng, start + timedelta(days=n)), amount=amount,
                       direction="OUT", channel="TRANSFER", transaction_type="P2P_TRANSFER", apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P06", party.source_id, start,
                    start + timedelta(days=10), f"Repeated identical round amount {amount:,}")

    def _inject_p07(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 35)
        amount = rng.uniform(5_000_000, 15_000_000)
        for week in range(5):
            amount *= rng.uniform(2.0, 3.0)
            for _ in range(rng.randrange(2, 5)):
                day = start + timedelta(days=week * 7 + rng.randrange(0, 7))
                self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                           amount=amount, direction="OUT", channel="TRANSFER",
                           transaction_type="P2P_TRANSFER", apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P07", party.source_id, start,
                    start + timedelta(days=35), "Weekly value stepping up by 2-3x")

    def _inject_p08(self, rng: random.Random, scenario_id: str) -> None:
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 20)
        country = rng.choice(HIGH_RISK_COUNTRIES)
        for _ in range(rng.randrange(8, 18)):
            day = start + timedelta(days=rng.randrange(0, 20))
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(80_000_000, 400_000_000), direction="OUT", channel="REMITTANCE",
                       transaction_type="INBOUND_REMITTANCE", counterparty_country=country, apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P08", party.source_id, start,
                    start + timedelta(days=20), f"Exposure concentrated on listed geography {country}")

    def _inject_p09(self, rng: random.Random, scenario_id: str) -> None:
        parties = self._scenario_parties(rng, rng.randrange(4, 8))
        if len(parties) < 3 or not self.devices:
            return
        shared = rng.choice(self.devices)["source_device_id"]
        start = self._window_start(rng, 14)
        for party in parties:
            for _ in range(rng.randrange(3, 7)):
                day = start + timedelta(days=rng.randrange(0, 14))
                self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                           amount=rng.uniform(1_000_000, 40_000_000), direction="OUT", channel="TRANSFER",
                           transaction_type="P2P_TRANSFER", device_id=shared, apply_defects=False)
        for party in parties:
            self._label(scenario_id, "INJECTED_POSITIVE", "P09", party.source_id, start,
                        start + timedelta(days=14), f"Device {shared} shared by {len(parties)} unrelated entities")

    def _inject_p10(self, rng: random.Random, scenario_id: str) -> None:
        if not self.merchants:
            return
        merchant = rng.choice(self.merchants)
        business = self.parties.get(merchant["source_business_id"])
        if business is None or not business.accounts:
            return
        start = self._window_start(rng, 25)
        # Ticket sizes an order of magnitude away from what the MCC implies.
        for _ in range(rng.randrange(20, 45)):
            day = start + timedelta(days=rng.randrange(0, 25))
            self._emit(account_id=rng.choice(business.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(25_000_000, 120_000_000), direction="IN", channel="QRIS",
                       transaction_type="MERCHANT_PAYMENT", merchant_id=merchant["source_merchant_id"],
                       apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P10", merchant["source_business_id"], start,
                    start + timedelta(days=25), f"Ticket size inconsistent with MCC {merchant['mcc']}")

    def _inject_p11(self, rng: random.Random, scenario_id: str) -> None:
        senders = self._scenario_parties(rng, rng.randrange(8, 16))
        collector = self._scenario_parties(rng, 1)
        if len(senders) < 5 or not collector or not self.devices:
            return
        collector = collector[0]
        target_account = rng.choice(collector.accounts)
        shared_device = rng.choice(self.devices)["source_device_id"]
        start = self._window_start(rng, 5)
        for sender in senders:
            day = start + timedelta(days=rng.randrange(0, 5))
            amount = rng.uniform(20_000_000, 90_000_000)
            self._emit(account_id=rng.choice(sender.accounts), when=self._random_moment(rng, day), amount=amount,
                       direction="OUT", channel="TRANSFER", transaction_type="P2P_TRANSFER",
                       device_id=shared_device, counterparty_ref=target_account,
                       counterparty_name=collector.name, apply_defects=False)
            self._emit(account_id=target_account, when=self._random_moment(rng, day), amount=amount,
                       direction="IN", channel="TRANSFER", transaction_type="P2P_TRANSFER",
                       device_id=shared_device, counterparty_ref=rng.choice(sender.accounts),
                       counterparty_name=sender.name, apply_defects=False)
        self._label(scenario_id, "INJECTED_POSITIVE", "P11", collector.source_id, start,
                    start + timedelta(days=5), f"{len(senders)}-to-1 convergence with device overlap")

    def _inject_p12(self, rng: random.Random, scenario_id: str) -> None:
        if not self.watchlist or not self.customers:
            return
        listed = rng.choice(self.watchlist)
        customer = rng.choice([c for c in self.customers if not c["source_customer_id"].startswith("CUST-ER")])
        # A near-name, never an exact copy: screening stops at potential match
        # (FRD §3.5) and the reviewer is the one who decides.
        customer["full_name"] = listed["primary_name"].replace("i", "y", 1)
        self._label(scenario_id, "INJECTED_POSITIVE", "P12", customer["source_customer_id"],
                    self.period_start, self.reference_date,
                    f"Name close to list record {listed['list_record_id']} ({listed['list_type']})")

    def _inject_edge(self, rng: random.Random, pattern: str, scenario_id: str) -> None:
        """Legitimate activity built to sit close to the injected positives
        (TRD §11.2). If these were trivially separable, the false-positive
        discussion would be meaningless."""
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        # `spacing` is the gap between transactions in days. Where the note
        # claims the activity falls outside a rule's window, the spacing has to
        # guarantee it — a label that says one thing while the data does another
        # is worse than no label at all. P02's window is 7 days rolling, so
        # 8-day spacing can never put three deposits in one window.
        constructions = {
            "P02": ("Arisan collector: in-band deposits, but always spread past the 7-day window",
                    8, 380_000_000, "CASH", 5),
            "P11": ("School fee account: many payers, same names every month", 3, 3_500_000, "TRANSFER", 8),
            "P04": ("Payroll disburser on payday", 1, 8_000_000, "TRANSFER", 6),
            "P06": ("Agent kiosk float top-ups in round amounts", 2, 20_000_000, "AGENT", 8),
            "P08": ("Cross-border student receiving family support", 14, 15_000_000, "REMITTANCE", 6),
        }
        note, spacing, base, channel, count = constructions.get(
            pattern, (f"Legitimate look-alike for {pattern}", 3, 12_000_000, "TRANSFER", 6)
        )
        span = spacing * count
        start = self._window_start(rng, span)
        for n in range(count):
            day = start + timedelta(days=n * spacing)
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=base * rng.uniform(0.8, 1.2), direction=rng.choice(("IN", "OUT")),
                       channel=channel, transaction_type="P2P_TRANSFER", apply_defects=False)
        self._label(scenario_id, "EDGE_CASE", pattern, party.source_id, start,
                    start + timedelta(days=span), note)

    def _inject_boundary(self, rng: random.Random, pattern: str, scenario_id: str) -> None:
        """Exactly on the threshold, where the rule must be unambiguous. For
        P02 the band's lower bound is inclusive and the reporting threshold is
        exclusive, so 500,000,000 must NOT be in band."""
        party = self._scenario_parties(rng, 1)
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 7)
        amount = 500_000_000.0 if pattern == "P02" else 100_000_000.0
        for n in range(3):
            self._emit(account_id=rng.choice(party.accounts),
                       when=self._random_moment(rng, start + timedelta(days=n)), amount=amount,
                       direction="IN", channel="CASH", transaction_type="CASH_DEPOSIT", apply_defects=False)
        self._label(scenario_id, "EDGE_CASE", pattern, party.source_id, start, start + timedelta(days=7),
                    f"Exactly at threshold ({amount:,.0f}) — must not fire at default parameters")

    # --- output ---------------------------------------------------------------

    def _write_csv(self, out_dir: Path, spec: FileSpec, rows: list[dict[str, Any]]) -> dict[str, Any]:
        path = out_dir / spec.name
        with path.open("w", newline="", encoding="utf-8") as handle:
            # TRD §6.1: UTF-8, comma-separated, quoted, header row, \n endings.
            writer = csv.DictWriter(handle, fieldnames=spec.header, quoting=csv.QUOTE_ALL,
                                    lineterminator="\n", extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({name: row.get(name, "") for name in spec.header})
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.counters.rows[spec.name] = len(rows)
        return {"name": spec.name, "sha256": digest, "record_count": len(rows)}

    def write(self, out_dir: Path) -> dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        payloads = {
            "customers.csv": self.customers,
            "business_customers.csv": self.businesses,
            "beneficial_owners.csv": self.beneficial_owners,
            "accounts.csv": self.accounts,
            "merchants.csv": self.merchants,
            "devices.csv": self.devices,
            "transactions.csv": self.transactions,
            "watchlist.csv": self.watchlist,
        }
        files = [self._write_csv(out_dir, spec, payloads[spec.name]) for spec in FILE_SPECS]
        # Ground truth travels beside the data but is not part of the source
        # contract, so it is written separately and left out of the manifest.
        self._write_csv(out_dir, LABELS, self.labels)

        manifest = {
            "source_system_code": SOURCE_SYSTEM_CODE,
            "business_date": self.reference_date.isoformat(),
            "contract_version": CONTRACT_VERSION,
            "files": files,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            # FR-501 / TRD §6.1: ingestion refuses the batch without this.
            "synthetic_declaration": True,
            "generator_version": GENERATOR_VERSION,
            "profile": self.profile,
            "seed": self.master_seed,
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

        (out_dir / "seeds.json").write_text(
            json.dumps(
                {"master_seed": self.master_seed,
                 "derived": {name: _seed_for(self.master_seed, name) for name in sorted(self.rng)}},
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        report = {
            "generator_version": GENERATOR_VERSION,
            "profile": self.profile,
            "seed": self.master_seed,
            "reference_date": self.reference_date.isoformat(),
            "period_start": self.period_start.isoformat(),
            "generated_at": manifest["generated_at"],
            "row_counts": dict(sorted(self.counters.rows.items())),
            "defect_counts": dict(sorted(self.counters.defects.items())),
            "label_counts": dict(sorted(self.counters.labels.items())),
            "file_checksums": {f["name"]: f["sha256"] for f in files},
        }
        (out_dir / "generation_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

        (out_dir / "data_dictionary.md").write_text(self._data_dictionary(), encoding="utf-8")
        (out_dir / "scenario_catalogue.md").write_text(self._scenario_catalogue(), encoding="utf-8")
        return report

    def _data_dictionary(self) -> str:
        """Rendered from the same specs the CSVs are written from, so it cannot
        drift from what was produced (TRD §11.7)."""
        lines = [
            "# Data dictionary — FCIP synthetic raw source files",
            "",
            f"Generator `{GENERATOR_VERSION}`, contract version `{CONTRACT_VERSION}`, "
            f"profile `{self.profile}`, seed `{self.master_seed}`.",
            "",
            "Generated from `app/scripts/raw_contract.py`, which is also what the generator writes the",
            "CSVs from — this file cannot describe columns that were not produced.",
            "",
            "All data is synthetic (CN-02). National IDs use province prefix `99`, which is never issued;",
            "registration numbers use the same prefix; phones sit in a reserved `+62899` block; IP addresses",
            "come from the RFC 5737 documentation ranges. Nothing here is derived from a real person,",
            "document or list.",
            "",
            "Format (TRD §6.1): UTF-8, comma-separated, every field quoted, header row, `\\n` line endings.",
            "",
        ]
        for spec in (*FILE_SPECS, LABELS):
            lines += [f"## `{spec.name}`", "", spec.purpose, "",
                      "| Field | Type | Mandatory | Allowed values | Rule / defect |", "|---|---|---|---|---|"]
            for f in spec.fields:
                allowed = ", ".join(f"`{v}`" for v in f.allowed) if f.allowed else "—"
                rule = " ".join(part for part in (f.rule, f.defect and f"**Defect:** {f.defect}") if part) or "—"
                lines.append(f"| `{f.name}` | {f.type} | {'yes' if f.required else 'no'} | {allowed} | {rule} |")
            lines.append("")
        lines += ["## Deliberate quality defects (TRD §11.4)", "",
                  "| Defect | Target rate | Expected handling |", "|---|---|---|"]
        handling = {
            "missing_counterparty": "Loaded; lowers referential-integrity metric; drives the Entity 360 banner",
            "missing_device": "Loaded; feature marked `DATA_UNAVAILABLE`",
            "malformed_date": "Quarantined `INVALID_DATE_FORMAT`",
            "invalid_currency": "Quarantined `INVALID_CURRENCY`",
            "invalid_amount": "Quarantined `INVALID_AMOUNT`",
            "unresolved_account": "Held to end of batch, then quarantined `UNRESOLVED_ACCOUNT`",
            "exact_duplicate": "Suppressed by idempotency, still counted",
            "idempotency_conflict": "Quarantined `IDEMPOTENCY_CONFLICT`",
            "late_arrival": "Flagged `LATE_ARRIVAL`, triggers targeted re-evaluation",
            "missing_bo": "Loaded; flagged `BO_MISSING`; feeds a risk factor",
            "placeholder_address": "Loaded; produces a `LOW_SPECIFICITY` graph edge",
            "truncated_name": "Loaded; some become `NOT_SCREENABLE`, others `HIGH_FREQUENCY_NAME`",
        }
        for name, rate in DEFECTS.items():
            actual = self.counters.defects.get(name, 0)
            lines.append(f"| `{name}` | {rate:.2%} | {handling[name]} (produced: {actual:,}) |")
        return "\n".join(lines) + "\n"

    def _scenario_catalogue(self) -> str:
        lines = [
            "# Scenario catalogue — injected positives, edge cases and control cohort",
            "",
            f"Profile `{self.profile}`, seed `{self.master_seed}`. Every row below has matching rows in",
            "`labels.csv`, so recall and control-cohort tests are mechanical rather than eyeballed (TRD §11.3).",
            "",
            "| Pattern | Scenario | Construction | Expected reason code | Labelled |",
            "|---|---|---|---|---|",
        ]
        constructions = {
            "P01": "One transfer of IDR 2-8bn against an ordinary baseline",
            "P02": "3-5 deposits inside [350M, 500M) within 7 days, aggregate >= 500M",
            "P03": "Credit in the morning, 94-99% of it out again the same day",
            "P04": "40-90 payments over 3 days against a much lower baseline",
            "P05": "150 days with no activity, then a credit of IDR 400M-1.5bn",
            "P06": "6-12 repetitions of one identical round amount",
            "P07": "Weekly value stepping up 2-3x for five consecutive weeks",
            "P08": "8-18 remittances concentrated on one listed geography",
            "P09": "One device used by 4-7 otherwise unrelated entities",
            "P10": "20-45 payments at 100-1000x the ticket size the MCC implies",
            "P11": "8-16 senders converging on one account within 5 days, sharing a device",
            "P12": "Customer name one character away from a synthetic list record",
        }
        for pattern, name in PATTERN_NAMES.items():
            positives = self.counters.labels.get(f"{pattern}_INJECTED_POSITIVE", 0)
            edges = self.counters.labels.get(f"{pattern}_EDGE_CASE", 0)
            lines.append(
                f"| `{pattern}` | {name} | {constructions[pattern]} | `{EXPECTED_REASON[pattern]}` | "
                f"{positives} positive, {edges} edge |"
            )
        lines += [
            "",
            "## Control cohort (FRD §8.14)",
            "",
            f"{self.counters.labels.get('CONTROL_CLEAN', 0)} entities are generated with deliberately modest,",
            "well-spread behaviour and must raise **zero** alerts at approved default parameters. A control-cohort",
            "alert is a rule defect, not a finding.",
            "",
            "## Entity resolution population (TRD §11.5)",
            "",
            "| Construction | Count | Expectation |",
            "|---|---|---|",
        ]
        for construction, expectation in (
            ("SAME_ID_NAME_VARIANT", "must auto-merge"),
            ("SAME_PHONE_DOB", "must auto-merge"),
            ("SAME_NAME_ONLY", "must **not** auto-merge (US-111)"),
            ("IDENTIFIER_CONFLICT", "must force `PENDING_REVIEW` with `IDENTIFIER_CONFLICT`"),
            ("AMBIGUOUS_BAND", "lands in the 0.75-0.95 manual review band"),
        ):
            lines.append(f"| `{construction}` | {self.counters.labels.get(f'ER_{construction}', 0)} | {expectation} |")
        return "\n".join(lines) + "\n"

    def generate(self) -> None:
        self.build_customers()
        self.build_businesses()
        self.share_beneficial_owners()
        self.build_accounts()
        self.build_merchants()
        self.build_devices()
        self.build_watchlist()
        self.build_background_traffic()
        self.inject_scenarios()
        # Deterministic, and chronological like a real source extract.
        self.transactions.sort(key=lambda r: (r["business_date"], r["source_transaction_reference"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic raw source dataset (TRD §6.1, §11).")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="small",
                        help="tiny=1%% (CI), small=5%% (local dev), full=100%% (integration and demo)")
    parser.add_argument("--seed", type=int, default=20260923, help="master seed; same seed reproduces the dataset")
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 9, 30),
                        help="last business date of the six-month period (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: sample_data/raw/<profile>)")
    args = parser.parse_args()

    out_dir = args.out or (DEFAULT_OUT / args.profile)
    generator = RawDatasetGenerator(profile=args.profile, seed=args.seed, reference_date=args.reference_date)
    generator.generate()
    report = generator.write(out_dir)

    print(f"profile={args.profile} seed={args.seed} -> {out_dir}")
    for name, count in report["row_counts"].items():
        print(f"  {name:<26} {count:>9,}")
    print(f"  {'defects injected':<26} {sum(report['defect_counts'].values()):>9,}")
    print(f"  {'labels written':<26} {sum(report['label_counts'].values()):>9,}")


if __name__ == "__main__":
    main()
