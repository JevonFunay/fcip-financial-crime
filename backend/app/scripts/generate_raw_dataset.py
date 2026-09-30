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
    python -m app.scripts.generate_raw_dataset --target-transactions 100000
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.scripts.raw_contract import (
    CONTRACT_VERSION,
    FILE_SPECS,
    GENERATOR_VERSION,
    LABEL_TRANSACTIONS,
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

# Custom scale: every volume is derived from a requested transaction count by
# the same ratio as the full profile (target / 420,000), so nothing new is
# hardcoded. Unlike the presets, it floors each pattern's scenario counts so
# every pattern keeps enough samples to test, and it calibrates the row count
# onto the target exactly.
CUSTOM_PROFILE = "custom"
DEFAULT_MIN_PER_PATTERN = 5
# Every labelled entity carries exactly one scenario, so the per-pattern floor
# needs a minimum number of distinct parties. Below this target the population
# is too small to place them all and pure proportion stops holding.
MIN_TARGET_TRANSACTIONS = 20_000

# TRD §11.3 injected-scenario counts at the full profile, plus ATO.
SCENARIO_TARGETS = {
    "P01": 60, "P02": 45, "P03": 50, "P04": 55, "P05": 40, "P06": 35,
    "P07": 40, "P08": 45, "P09": 30, "P10": 45, "P11": 35, "P12": 70,
    # Account takeover: the Fraud use case the mentor asked for (PROJECT_CONTEXT
    # §8). A project extension, not one of the FRD's twelve patterns.
    "ATO": 60,
}
EDGE_PER_PATTERN = 25
BOUNDARY_PER_PATTERN = 5
ATO_EDGE_CASES = 30
# Every kind of scenario per pattern, at the full profile. ATO has its own
# look-alikes and no threshold, so no boundary cases.
# Boundary cases sit exactly on a pattern's own FRD §8 threshold, on the side
# that must not fire. Only where one clean threshold exists: P07 needs a
# high-value baseline no ordinary customer has, P08 and P10 fire on any of
# several OR-ed sub-conditions, and P12 is the screening engine's.
BOUNDARY_PATTERNS = ("P01", "P02", "P03", "P04", "P05", "P06", "P09", "P11")
SCENARIO_PLAN = {
    **{pattern: {"positives": count, "edge": EDGE_PER_PATTERN,
                 **({"boundary": BOUNDARY_PER_PATTERN} if pattern in BOUNDARY_PATTERNS else {})}
       for pattern, count in SCENARIO_TARGETS.items() if pattern != "ATO"},
    "ATO": {"positives": SCENARIO_TARGETS["ATO"], "edge": ATO_EDGE_CASES},
}

# TRD §11.1: "~120 device yang sengaja digunakan bersama". Every transacting
# party otherwise has a device of its own; these few are household devices
# also used by one or two other parties. (1.1.0 handed devices out at random,
# so thousands were shared by unrelated parties, and a device with one user
# looked unusual.)
SHARED_HOUSEHOLD_DEVICES = 120
# Ordinary customers change phones: this share of individuals starts using a
# new device part-way through the period. Without it "a device never seen for
# this customer" would only ever happen in an ATO scenario, and a model would
# learn the scenario instead of the behaviour.
DEVICE_CHANGE_SHARE = 0.15
# TRD §11.2: ordinary retail customers "transfer ke sekumpulan counterparty
# kecil yang stabil", businesses and merchants have "basis pembayar yang luas".
# 1.2.0 drew a fresh random counterparty for every row, so every payment went
# to someone new and "a recipient never paid before" could not tell an ATO
# drain from ordinary life. Each party now has regulars; a share of payments
# still goes to someone new, the way it does in real life, so a new recipient
# is ordinary too, just rarer.
RETAIL_REGULARS = (3, 8)            # stable counterparties per retail customer
FAVOURITE_MERCHANTS = (4, 10)       # merchants a retail customer usually pays
NEW_COUNTERPARTY_RATE = 0.10        # retail payments to someone new
NEW_BECOMES_REGULAR = 0.25          # ...of which this share becomes a regular
NEW_PAYER_RATE = 0.30               # a business's incoming payments from a first-time payer
# TRD §11.2, retail: "pembayaran bernilai kecil yang sering, top-up, transfer
# ...". 1.3.0 gave every party about the same number of rows (CV 0.35, the
# busiest tenth held 16% of them); real wallets are heavy-tailed. Activity is
# drawn per party from a lognormal with mean 1, so the TRD §11.1 total holds
# while a minority transacts often.
ACTIVITY_SIGMA = 1.0
# Merchants take many payments ("basis pembayar yang luas"): a business-like
# party is this many times as active as a retail customer on average.
BUSINESS_ACTIVITY = 3.0
# Retail mix: (channel, type, weight). Direction follows the type: a top-up
# brings money in, a bill goes out, a merchant payment goes out (a few come
# back as refunds), transfers go both ways.
RETAIL_MIX = (("QRIS", "MERCHANT_PAYMENT", 40), ("TOPUP", "WALLET_TOPUP", 20),
              ("PAYMENT", "BILL_PAYMENT", 15), ("TRANSFER", "P2P_TRANSFER", 25))
REFUND_SHARE = 0.05
P2P_IN_SHARE = 0.40
TOPUP_UNIT = 50_000                 # top-ups are round (FRD §8.6 lists them as an expected P06 look-alike)
# A customer tops up from its own one or two bank accounts and pays the same
# one to three billers every month; P2P transfers go to its regulars. 1.3.0
# drew all three from one book, so a top-up could come from a friend.
TOPUP_SOURCES = (1, 2)
BILLS_PER_CUSTOMER = (1, 3)
BILLERS = ("Tagihan Listrik Prabayar", "Tagihan Air Kota", "Internet Rumah", "Pulsa Pascabayar",
           "Asuransi Kesehatan", "Cicilan Kendaraan", "Iuran Lingkungan", "TV Berlangganan")
# TRD §11.2, business: "pola settlement" and "nilai tiket yang konsisten dengan
# kategori". A business sweeps its takings to its own bank every week, and a
# payment is drawn around its merchant category's typical ticket.
# Scenario rows are layered on after background: about 4.1% of the Full file
# in 1.4.0 (17.2k of 424k on five seeds). The background leaves room for them
# so the file lands on the TRD §11.1 total, not above it.
SCENARIO_ROW_SHARE = 0.041
# A business existed before the six-month window, so it starts it with an
# established payer base (a third of its payment count), not an empty book
# whose first few payers would dominate its first months (1.4.0 drafts had
# 116 ordinary businesses over P10's PAYER_CONCENTRATION 0.70).
PAYER_BASE_SHARE = 1 / 3
# A merchant takes payments while it is open, with the odd late order.
OUT_OF_HOURS_SHARE = 0.03
# A company pays on its own day of the month, on the Friday before when that
# falls on a weekend.
PAYROLL_DAYS = (23, 24, 25, 26, 27, 28)
SETTLEMENT_EVERY_DAYS = 7
TICKET_SPREAD = 0.45                # lognormal sigma around the category's typical ticket
# FRD §8.5 DORMANCY_DAYS >= 90; the ATO dormancy is drawn from this range.
ATO_DORMANCY_DAYS = (90, 150)
# ATO look-alikes, cycled in this order: which of the three takeover signals
# each one has (dormancy, a new device, a burst), and whether it needs a
# device nobody has used before.
ATO_EDGE_KINDS = (
    ("RETURNING_NEW_PHONE", True, "back after {days} quiet days on a new phone, ordinary spending"),
    ("NEW_PHONE_PAYDAY", True, "new phone on payday, a burst of routine payments"),
    ("RETURNING_SAME_PHONE", False, "back after {days} quiet days on the usual phone, a few ordinary payments"),
)

# TRD §11.2 population mix, as exact shares of all non-ER parties; retail takes
# the remainder (~62%). §11.3's "~800 CONTROL_CLEAN entities" at the full
# profile is this 8% of ~10,400 parties, not a separate target.
COHORT_SHARES = {
    "BUSINESS_NORMAL": 0.18,
    "CONTROL_CLEAN": 0.08,
    "EDGE_AMBIGUOUS": 0.08,
    "INJECTED_CANDIDATE": 0.04,
}
COHORT_ORDER = ("RETAIL_NORMAL", "BUSINESS_NORMAL", "CONTROL_CLEAN", "EDGE_AMBIGUOUS", "INJECTED_CANDIDATE")
UNASSIGNED = "UNASSIGNED"

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
    "ATO": "Account takeover after dormancy on a new device",
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
    # An ordinary phone change: from switch_day on, mostly switch_device.
    switch_day: date | None = None
    switch_device: str | None = None
    # TRD §11.2: regular counterparties (retail) or payer base (business), as
    # (reference, name, first day in use), and a retail customer's usual
    # merchants as (merchant, first day). A row may only use one already in
    # use on its date, or it would make a later first appearance look old.
    counterparties: list[tuple[str, str, date]] = field(default_factory=list)
    merchants: list[tuple[str, date]] = field(default_factory=list)
    # A sole trader has no merchant record, so it gets a category's ticket.
    category_ticket: float | None = None
    funding: list[tuple[str, str]] = field(default_factory=list)
    billers: list[int] = field(default_factory=list)


@dataclass
class Counters:
    rows: dict[str, int] = field(default_factory=dict)
    labels: dict[str, int] = field(default_factory=dict)

    def label(self, name: str, n: int = 1) -> None:
        self.labels[name] = self.labels.get(name, 0) + n


class RawDatasetGenerator:
    def __init__(
        self,
        *,
        profile: str | None = None,
        seed: int,
        reference_date: date,
        target_transactions: int | None = None,
        min_per_pattern: int = DEFAULT_MIN_PER_PATTERN,
    ) -> None:
        if target_transactions is not None:
            if profile not in (None, CUSTOM_PROFILE):
                raise ValueError("pass either a profile or target_transactions, not both")
            if target_transactions < MIN_TARGET_TRANSACTIONS:
                raise ValueError(
                    f"target_transactions must be at least {MIN_TARGET_TRANSACTIONS:,}: below that the population "
                    f"is too small to place {min_per_pattern} scenarios per pattern on distinct entities"
                )
            if min_per_pattern < 1:
                raise ValueError("min_per_pattern must be at least 1")
            self.profile = CUSTOM_PROFILE
            self.factor = target_transactions / FULL_TARGETS["transactions"]
        else:
            if profile not in PROFILES:
                raise ValueError(f"unknown profile {profile!r}; choose from {', '.join(PROFILES)}")
            self.profile = profile
            self.factor = PROFILES[profile]
        self.target_transactions = target_transactions
        self.min_per_pattern = min_per_pattern
        self._floor_adjustments: list[dict[str, Any]] = []
        self._requested_scenarios: dict[str, dict[str, int]] = {}
        self._calibration: dict[str, int] = {}
        self.master_seed = seed
        self.reference_date = reference_date
        # TRD §11.1: six months ending on the demo reference date. The longest
        # rule window is 90 days (AS-06), so this leaves a full baseline behind
        # every scenario placed in the second half.
        self.period_start = reference_date - timedelta(days=182)
        self.rng = {
            name: random.Random(_seed_for(seed, name))
            for name in ("party", "account", "merchant", "device", "txn", "defect", "scenario", "watchlist", "cohort",
                         "counterparty")
        }
        self.counters = Counters()
        # Scenario placement state: see _take().
        self._pools: dict[str, list[Party]] = {}
        self._claimed: set[str] = set()
        self._placement: dict[str, Counter[str]] = {"INJECTED_CANDIDATE": Counter(), "EDGE_AMBIGUOUS": Counter()}
        self._without_bo: set[str] = set()
        self._customer_rows: dict[str, dict[str, Any]] = {}
        self.customers: list[dict[str, Any]] = []
        self.businesses: list[dict[str, Any]] = []
        self.beneficial_owners: list[dict[str, Any]] = []
        self.accounts: list[dict[str, Any]] = []
        self.merchants: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self.transactions: list[dict[str, Any]] = []
        self.watchlist: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.label_transactions: list[dict[str, str]] = []
        self.parties: dict[str, Party] = {}
        self._txn_seq = 0
        # The scenario whose rows _emit is currently writing (label_transactions).
        self._scenario: str | None = None
        self._account_party: dict[str, Party] = {}
        self._own_merchants: dict[str, list[str]] = {}
        self._merchant_counterparty: dict[str, tuple[str, str]] = {}
        self._biller_counterparty: dict[int, tuple[str, str]] = {}
        self._merchant_ticket: dict[str, float] = {}
        self._merchant_hours: dict[str, range] = {}
        # Dormancy gaps carved once, after every scenario is placed: (accounts,
        # first business date, first business date after the gap).
        self._gaps: list[tuple[set[str], str, str]] = []

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
            return rng.choice(PLACEHOLDER_ADDRESSES), city, province, postcode
        return f"{rng.choice(STREETS)} No. {rng.randrange(1, 240)}", city, province, postcode

    def _maybe_truncate(self, rng: random.Random, name: str) -> str:
        """TRD §11.4: 3% of parties get a single-token or truncated name, which
        later becomes NOT_SCREENABLE or HIGH_FREQUENCY_NAME."""
        if rng.random() >= DEFECTS["truncated_name"]:
            return name
        return name.split()[-1] if rng.random() < 0.5 else name[: max(3, len(name) // 2)].strip()

    # --- populations ----------------------------------------------------------

    def assign_cohorts(self) -> None:
        """TRD §11.2 population mix, assigned as exact counts rather than drawn
        per party, so the shares hold at every scale including the CI profile.

        Every real business is in the business cohort. §11.1's volumes put real
        businesses at ~11% of parties (1,200 of ~10,400) against §11.2's ~18%,
        so the rest of the business cohort is individual sole traders (UMKM
        wallet merchants), who transact like merchants. Control, edge and
        injected cohorts are drawn from individuals.
        """
        rng = self.rng["cohort"]
        parties = [p for p in self.parties.values() if p.cohort != "ER_TEST"]
        total = len(parties)
        individuals = [p for p in parties if not p.is_business]
        for party in parties:
            if party.is_business:
                party.cohort = "BUSINESS_NORMAL"
        rng.shuffle(individuals)
        quotas = (
            ("BUSINESS_NORMAL", max(0, round(total * COHORT_SHARES["BUSINESS_NORMAL"]) - (total - len(individuals)))),
            ("CONTROL_CLEAN", round(total * COHORT_SHARES["CONTROL_CLEAN"])),
            ("EDGE_AMBIGUOUS", round(total * COHORT_SHARES["EDGE_AMBIGUOUS"])),
            ("INJECTED_CANDIDATE", round(total * COHORT_SHARES["INJECTED_CANDIDATE"])),
        )
        cursor = 0
        for cohort, quota in quotas:
            for party in individuals[cursor:cursor + quota]:
                party.cohort = cohort
            cursor += quota
        for party in individuals[cursor:]:
            party.cohort = "RETAIL_NORMAL"

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
            full_name = self._person_name(rng, gender)
            name = self._maybe_truncate(rng, full_name)
            # Born somewhere, living somewhere else most of the time.
            birth_city = city if rng.random() < 0.35 else rng.choice(CITIES)[0]
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
                # Row-level defect marker; never written (see _defect_counts).
                "_defects": ["truncated_name"] if name != full_name else [],
            })
            # Cohort is assigned once the whole population exists (assign_cohorts).
            self.parties[source_id] = Party(source_id, name, UNASSIGNED)
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
                self._without_bo.add(source_id)
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
            # A business shipped without an owner must stay without one, or the
            # BO_MISSING defect would silently disappear.
            if other == donor["source_business_id"] or other in self._without_bo:
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
                self._account_party[account_id] = party

    def build_merchants(self) -> None:
        rng = self.rng["merchant"]
        businesses = [b["source_business_id"] for b in self.businesses]
        if not businesses:
            return
        # A business's outlets are one line of trade: one MCC for all its
        # merchants. 1.3.0 drew each merchant's MCC on its own, so one business
        # could be a hospital, a book store and a drug store, and its tickets
        # fit no single category (TRD §11.2 "konsisten dengan kategori").
        category: dict[str, tuple[int, str, int]] = {}
        for i in range(1, _scaled(FULL_TARGETS["merchants"], self.factor, 8) + 1):
            business_id = rng.choice(businesses)
            mcc, description, ticket = category.setdefault(business_id, rng.choice(MCC_BANDS))
            settlement = next(
                (a for a in self.parties[business_id].accounts), ""
            )
            self._own_merchants.setdefault(business_id, []).append(f"MER-{i:05d}")
            self._merchant_ticket[f"MER-{i:05d}"] = float(ticket)
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
                "operating_hours_declared": (hours := rng.choice(("08:00-17:00", "09:00-21:00", "00:00-23:59",
                                                                  "10:00-22:00"))),
                "outlet_count": rng.randrange(1, 12),
            })
            opens, closes = int(hours[:2]), int(hours[6:8]) + (hours[9:] == "59")
            self._merchant_hours[f"MER-{i:05d}"] = range(opens, closes)

    def _device_row(self, rng: random.Random, device_id: str) -> dict[str, Any]:
        first_seen = self.period_start - timedelta(days=rng.randrange(0, 400))
        device_type = rng.choices(("ANDROID_PHONE", "IOS_PHONE", "TABLET", "WEB_BROWSER"), weights=(70, 22, 5, 3))[0]
        return {
            "source_device_id": device_id,
            "device_type": device_type,
            "os_family": rng.choice(OS_FAMILIES) if "PHONE" in device_type else "Other",
            "app_version": f"{rng.randrange(3, 8)}.{rng.randrange(0, 20)}.{rng.randrange(0, 9)}",
            "is_emulator": "true" if rng.random() < 0.01 else "false",
            "is_rooted": "true" if rng.random() < 0.02 else "false",
            "first_seen": datetime.combine(first_seen, datetime.min.time(), JAKARTA).isoformat(),
            "last_seen": datetime.combine(self.reference_date, datetime.min.time(), JAKARTA).isoformat(),
        }

    def _new_device(self, rng: random.Random, first_seen: date | None = None) -> str:
        device = self._device_row(rng, f"DEV-{len(self.devices) + 1:06d}")
        if first_seen is not None:
            device["first_seen"] = datetime.combine(first_seen, datetime.min.time(), JAKARTA).isoformat()
        self.devices.append(device)
        return device["source_device_id"]

    def build_devices(self) -> None:
        """TRD §11.1: every transacting party has a device of its own, and ~120
        are deliberately shared. The total follows from the population, inside
        the TRD's 8,000-12,000 band at the full profile, rather than being
        forced to exactly 10,000, which would oblige thousands of unrelated
        parties to share. ER twins carry no traffic, so they get no device."""
        rng = self.rng["device"]
        transacting = [p for p in self.parties.values() if p.accounts and p.cohort != "ER_TEST"]
        for party in transacting:
            party.devices.append(self._new_device(rng))
        for _ in range(_scaled(SHARED_HOUSEHOLD_DEVICES, self.factor, 1) if transacting else 0):
            device = rng.choice(transacting).devices[0]
            for other in rng.sample(transacting, min(len(transacting), rng.randrange(1, 3))):
                if device not in other.devices:
                    other.devices.append(device)
        individuals = [p for p in transacting if not p.is_business]
        span = (self.reference_date - self.period_start).days - 44
        for party in rng.sample(individuals, round(len(individuals) * DEVICE_CHANGE_SHARE)):
            party.switch_day = self.period_start + timedelta(days=30 + rng.randrange(0, max(1, span)))
            party.switch_device = self._new_device(rng, party.switch_day)

    def _fresh_device(self, day: date) -> str:
        """A device nobody has used, first seen on `day`."""
        return self._new_device(self.rng["device"], day)

    def _device_at(self, rng: random.Random, party: Party, day: date) -> str | None:
        """The device `party` would be using on `day`, background or scenario
        alike, so a scenario row never looks different for want of one."""
        if party.switch_day is not None and day >= party.switch_day and rng.random() < 0.9:
            return party.switch_device
        if party.devices:
            return rng.choice(party.devices)
        return party.switch_device

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
        defects: str = "all",
        counterparty: str = "usual",
    ) -> dict[str, Any]:
        rng = self.rng["defect"]
        if counterparty_ref is None:
            counterparty_ref, usual_name = self._counterparty_for(
                account_id, merchant_id, counterparty, when.astimezone(JAKARTA).date(), transaction_type
            )
            counterparty_name = counterparty_name or usual_name
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
            "counterparty_reference": counterparty_ref,
            "counterparty_name": counterparty_name,
            "counterparty_country": counterparty_country,
            "source_merchant_id": merchant_id,
            "source_device_id": device_id or "",
            "ip_address": f"198.51.100.{rng.randrange(1, 254)}" if rng.random() < 0.6 else f"203.0.113.{rng.randrange(1, 254)}",
            "status_from_source": rng.choices(("SETTLED", "PENDING", "REVERSED"), weights=(96, 3, 1))[0],
        }

        applied: list[str] = []
        if defects == "all":
            # The defects that quarantine or move a row apply to background
            # traffic only, never to an injected scenario: a quarantined row
            # would silently change whether the scenario fires, and the label
            # would then be a lie.
            # Backdated well before the batch business date: the loader flags
            # LATE_ARRIVAL and computes lag_days (FR-106). Applied first so
            # that a row which is also malformed stays malformed. Never before
            # the period (TRD §11.1): 1.3.0 put 0.4% of rows up to 100 days
            # before it, so an entity's history seemed to start months before
            # the data did, and the empty weeks became its P07 and FR-401
            # baseline. A row in the first days has no room to be late.
            room = (when.astimezone(JAKARTA).date() - self.period_start).days
            if rng.random() < DEFECTS["late_arrival"] and room >= 5:
                when = when - timedelta(days=rng.randrange(5, min(100, room + 1)))
                row["value_datetime"] = when.isoformat()
                row["business_date"] = when.astimezone(JAKARTA).date().isoformat()
                applied.append("late_arrival")
            if rng.random() < DEFECTS["missing_counterparty"]:
                row["counterparty_reference"] = ""
                applied.append("missing_counterparty")
            if rng.random() < DEFECTS["missing_device"]:
                row["source_device_id"] = ""
                applied.append("missing_device")
            if rng.random() < DEFECTS["malformed_date"]:
                row["value_datetime"] = when.strftime("%d/%m/%Y %H:%M")
                applied.append("malformed_date")
            if rng.random() < DEFECTS["invalid_currency"]:
                row["currency_original"] = rng.choice(("IDRR", "ID", "1DR", "id r"))
                applied.append("invalid_currency")
            if rng.random() < DEFECTS["invalid_amount"]:
                row["amount_original"] = rng.choice(("0.00", "-125000.00"))
                applied.append("invalid_amount")
            if rng.random() < DEFECTS["unresolved_account"]:
                row["source_account_id"] = f"ACC-9{rng.randrange(10**5, 10**6 - 1)}"
                applied.append("unresolved_account")
        elif defects == "soft":
            # Scenario rows: only the defects that load normally. Without them
            # a scenario row would be the only kind that never lacks a device
            # or a counterparty, and a model would learn that instead of the
            # behaviour (the 1.1.0 artefact).
            if rng.random() < DEFECTS["missing_counterparty"]:
                row["counterparty_reference"] = ""
                applied.append("missing_counterparty")
            if rng.random() < DEFECTS["missing_device"]:
                row["source_device_id"] = ""
                applied.append("missing_device")
        else:
            raise ValueError(f"unknown defects mode {defects!r}")

        # Row-level defect marker; never written (see _defect_counts).
        row["_defects"] = applied
        self.transactions.append(row)
        if self._scenario is not None:
            self.label_transactions.append(
                {"scenario_id": self._scenario, "source_transaction_reference": row["source_transaction_reference"]}
            )
        return row

    def _one_off(self) -> tuple[str, str]:
        """Someone this party has never paid or been paid by."""
        rng = self.rng["counterparty"]
        return f"EXT-{rng.randrange(10**6, 10**7 - 1)}", self._person_name(rng)

    def _merchant_ref(self, merchant_id: str) -> tuple[str, str]:
        """A merchant is one counterparty, the same for everyone who pays it."""
        if merchant_id not in self._merchant_counterparty:
            name = next((m["merchant_name"] for m in self.merchants if m["source_merchant_id"] == merchant_id), merchant_id)
            ref, _ = self._one_off()
            self._merchant_counterparty[merchant_id] = (ref, name)
        return self._merchant_counterparty[merchant_id]

    def _counterparty_for(self, account_id: str, merchant_id: str, mode: str, day: date,
                          transaction_type: str = "") -> tuple[str, str]:
        """TRD §11.2. `mode` "new" forces a first-time counterparty (ATO pays
        recipients its victim never paid); "usual" follows the party's book as
        it stood on `day`: its own bank account for a top-up, its billers for
        a bill, its payers or regulars otherwise."""
        party = self._account_party.get(account_id)
        rng = self.rng["counterparty"]
        if mode == "new" or party is None:
            return self._one_off()
        if mode != "usual":
            raise ValueError(f"unknown counterparty mode {mode!r}")
        if transaction_type == "WALLET_TOPUP":
            return self._funding_for(party)
        if transaction_type == "BILL_PAYMENT":
            return self._biller_for(party)
        in_use = [(ref, name) for ref, name, since in party.counterparties if since <= day]
        if party.is_business or party.cohort == "BUSINESS_NORMAL":
            # A broad payer base: most payments from payers seen before, a
            # steady stream of first-time payers.
            if not in_use or rng.random() < NEW_PAYER_RATE:
                ref, name = self._one_off()
                party.counterparties.append((ref, name, day))
                return ref, name
            return rng.choice(in_use)
        if merchant_id:
            return self._merchant_ref(merchant_id)
        if not party.counterparties:
            # The first time this customer pays anyone: its regulars exist from here on.
            party.counterparties = [(*self._one_off(), day) for _ in range(rng.randint(*RETAIL_REGULARS))]
            in_use = [(ref, name) for ref, name, _ in party.counterparties]
        if not in_use or rng.random() < NEW_COUNTERPARTY_RATE:
            ref, name = self._one_off()
            if rng.random() < NEW_BECOMES_REGULAR:
                party.counterparties.append((ref, name, day))
            return ref, name
        return rng.choice(in_use)

    def _merchant_for(self, party: Party, day: date) -> str:
        """A merchant this retail customer usually pays, now and then a new one."""
        rng = self.rng["counterparty"]
        if not self._merchant_ids:
            return ""
        if not party.merchants:
            chosen = rng.sample(self._merchant_ids, min(len(self._merchant_ids), rng.randint(*FAVOURITE_MERCHANTS)))
            party.merchants = [(merchant, day) for merchant in chosen]
        usual = [merchant for merchant, since in party.merchants if since <= day]
        if not usual or rng.random() < NEW_COUNTERPARTY_RATE:
            merchant = rng.choice(self._merchant_ids)
            if rng.random() < NEW_BECOMES_REGULAR and merchant not in {m for m, _ in party.merchants}:
                party.merchants.append((merchant, day))
            return merchant
        return rng.choice(usual)

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

    # Mostly waking hours, with a lunchtime and evening bias, and a thin night
    # tail (~5%). With no night activity at all, any scenario row after
    # midnight would be one of a kind.
    HOUR_WEIGHTS = (1, 1, 1, 1, 1, 1, 2, 4, 5, 6, 7, 9, 8, 6, 5, 5, 6, 7, 9, 10, 8, 6, 4, 3)

    def _random_moment(self, rng: random.Random, day: date) -> datetime:
        hour = rng.choices(range(24), weights=self.HOUR_WEIGHTS)[0]
        return datetime(day.year, day.month, day.day, hour, rng.randrange(0, 60), rng.randrange(0, 60), tzinfo=JAKARTA)

    def _merchant_moment(self, rng: random.Random, merchant: str, day: date) -> datetime:
        """A payment while the merchant is open: the usual hour profile inside
        its declared hours (FRD §8.10 judges the share outside them)."""
        hours = self._merchant_hours.get(merchant)
        if hours is None or rng.random() < OUT_OF_HOURS_SHARE:
            return self._random_moment(rng, day)
        hour = rng.choices(hours, weights=[self.HOUR_WEIGHTS[h] for h in hours])[0]
        return datetime(day.year, day.month, day.day, hour, rng.randrange(0, 60), rng.randrange(0, 60), tzinfo=JAKARTA)

    def _burst(self, rng: random.Random, day: date, count: int, spread_hours: int) -> list[datetime]:
        """`count` moments on `day` within a few hours of each other, in order.

        The centre is drawn like any row's hour and each moment is drawn with
        the same hour weights inside the window, so a burst's rows have the
        hours ordinary rows have. (Adding offsets to a start time pushed 1.2.0's
        first ATO bursts late into the night.)"""
        centre = rng.choices(range(24), weights=self.HOUR_WEIGHTS)[0]
        low, high = max(0, centre - spread_hours // 2), min(23, centre + spread_hours // 2)
        hours = range(low, high + 1)
        weights = [self.HOUR_WEIGHTS[h] for h in hours]
        return sorted(
            datetime(day.year, day.month, day.day, rng.choices(hours, weights=weights)[0],
                     rng.randrange(0, 60), rng.randrange(0, 60), tzinfo=JAKARTA)
            for _ in range(count)
        )

    def build_background_traffic(self) -> None:
        """Ordinary behaviour for every cohort except the injected scenarios,
        which are layered on afterwards."""
        rng = self.rng["txn"]
        budget = _scaled(FULL_TARGETS["transactions"], self.factor, 400)
        active = [p for p in self.parties.values() if p.accounts and p.cohort != "ER_TEST"]
        if not active:
            return

        self._days = [
            self.period_start + timedelta(days=n) for n in range((self.reference_date - self.period_start).days + 1)
        ]
        self._day_weights = [self._day_weight(d) for d in self._days]
        self._merchant_ids = [m["source_merchant_id"] for m in self.merchants]

        # Heavy-tailed activity with the TRD §11.1 total held: weights with mean
        # 1 (x BUSINESS_ACTIVITY for merchants), scaled onto the budget left
        # after the weekly settlements. Control-clean entities get deliberately
        # modest, well-spread behaviour so they raise nothing at default
        # parameters (FRD §8.14): half the average, no tail.
        weight = {}
        for party in active:
            if party.cohort == "CONTROL_CLEAN":
                weight[party.source_id] = 0.5
            else:
                tail = rng.lognormvariate(-ACTIVITY_SIGMA ** 2 / 2, ACTIVITY_SIGMA)
                weight[party.source_id] = tail * (BUSINESS_ACTIVITY if self._business_like(party) else 1.0)
        total_weight = sum(weight.values())
        # Payments and the weekly sweeps they cause share one budget. A sweep
        # happens only in a week with takings, so the expected number follows
        # from each business's payment count; three passes settle it.
        weeks = len(self._days) / SETTLEMENT_EVERY_DAYS
        background = budget * (1 - SCENARIO_ROW_SHARE)
        payment_budget = background
        for _ in range(3):
            counts = {pid: max(3, round(payment_budget * w / total_weight)) for pid, w in weight.items()}
            sweeps = sum(weeks * (1 - (1 - 1 / weeks) ** counts[p.source_id]) for p in active if self._business_like(p))
            payment_budget = max(len(active) * 3, background - sweeps)
        for party in active:
            count = counts[party.source_id]
            # In date order, so the counterparty book grows the way it would:
            # a payer is new the first time it appears, not whenever it happened
            # to be drawn.
            takings = []
            if self._business_like(party):
                party.counterparties = [(*self._one_off(), self.period_start)
                                        for _ in range(max(5, round(count * PAYER_BASE_SHARE)))]
            for day in sorted(rng.choices(self._days, weights=self._day_weights, k=count)):
                row = self._emit_background(rng, party, day)
                if row["direction"] == "IN":
                    takings.append((day, row))
            if self._business_like(party):
                self._settle(rng, party, takings)
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

    def _business_like(self, party: Party) -> bool:
        # Sole traders are individuals in the business cohort: they transact
        # like merchants rather than like retail customers.
        return party.is_business or party.cohort == "BUSINESS_NORMAL"

    def _emit_background(self, rng: random.Random, party: Party, day: date | None = None) -> dict[str, Any]:
        """One row of ordinary behaviour for `party`, defects drawn at the
        declared rates. Shared by background traffic and calibration."""
        if day is None:
            day = rng.choices(self._days, weights=self._day_weights)[0]
        account = rng.choice(party.accounts)
        device = self._device_at(rng, party, day)
        if self._business_like(party):
            # A payment through the business's own merchant, while it is open,
            # at a ticket its category makes ordinary (TRD §11.2). A sole
            # trader has no merchant record: its customers pay it by transfer.
            own = self._own_merchants.get(party.source_id)
            if own:
                merchant = rng.choice(own)
                typical = self._merchant_ticket[merchant]
                channel, ttype = "PAYMENT", "MERCHANT_PAYMENT"
                when = self._merchant_moment(rng, merchant, day)
            else:
                if party.category_ticket is None:
                    party.category_ticket = float(rng.choice(MCC_BANDS)[2])
                merchant, typical = "", party.category_ticket
                channel, ttype = "TRANSFER", "P2P_TRANSFER"
                when = self._random_moment(rng, day)
            amount = rng.lognormvariate(math.log(typical), TICKET_SPREAD)
            direction = "IN"
        else:
            when = self._random_moment(rng, day)
            channel, ttype = rng.choices([(c, t) for c, t, _ in RETAIL_MIX], weights=[w for *_, w in RETAIL_MIX])[0]
            if ttype == "MERCHANT_PAYMENT":
                amount = rng.lognormvariate(10.9, 1.0)
                direction = "IN" if rng.random() < REFUND_SHARE else "OUT"
                merchant = self._merchant_for(party, day)
            elif ttype == "WALLET_TOPUP":
                amount = max(1, round(rng.lognormvariate(12.2, 0.8) / TOPUP_UNIT)) * TOPUP_UNIT
                direction, merchant = "IN", ""
            elif ttype == "BILL_PAYMENT":
                amount = rng.lognormvariate(11.9, 0.6)
                direction, merchant = "OUT", ""
            else:
                amount = rng.lognormvariate(11.8, 1.2)
                direction = "IN" if rng.random() < P2P_IN_SHARE else "OUT"
                merchant = ""
        if party.cohort == "CONTROL_CLEAN":
            amount = min(amount, 120_000_000)
        return self._emit(
            account_id=account, when=when, amount=max(1000.0, amount), direction=direction,
            channel=channel, transaction_type=ttype, device_id=device, merchant_id=merchant,
        )

    def _funding_for(self, party: Party) -> tuple[str, str]:
        """The customer's own bank account a top-up comes from, in its own name."""
        rng = self.rng["counterparty"]
        if not party.funding:
            party.funding = [(self._one_off()[0], party.name) for _ in range(rng.randint(*TOPUP_SOURCES))]
        return rng.choice(party.funding)

    def _biller_for(self, party: Party) -> tuple[str, str]:
        """One of the customer's usual billers. Like a merchant, a biller is
        one counterparty for everyone who pays it."""
        rng = self.rng["counterparty"]
        if not party.billers:
            party.billers = rng.sample(range(len(BILLERS)), rng.randint(*BILLS_PER_CUSTOMER))
        index = rng.choice(party.billers)
        if index not in self._biller_counterparty:
            self._biller_counterparty[index] = (self._one_off()[0], BILLERS[index])
        return self._biller_counterparty[index]

    def _settle(self, rng: random.Random, party: Party, takings: list[tuple[date, dict[str, Any]]]) -> None:
        """TRD §11.2 "pola settlement": every week the business sweeps what it
        took since the last sweep to its own bank account, the same one each time."""
        if not takings:
            return
        bank_ref, bank_name = self._one_off()
        offset = rng.randrange(SETTLEMENT_EVERY_DAYS)
        sweep_days = [d for d in self._days if (d - self.period_start).days % SETTLEMENT_EVERY_DAYS == offset]
        position = 0
        for day in sweep_days:
            total = 0.0
            while position < len(takings) and takings[position][0] <= day:
                try:
                    total += float(takings[position][1]["amount_original"])
                except ValueError:
                    pass
                position += 1
            if total <= 0:
                continue
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=max(1000.0, total * rng.uniform(0.97, 1.0)), direction="OUT", channel="TRANSFER",
                       transaction_type="SETTLEMENT", device_id=self._device_at(rng, party, day),
                       counterparty_ref=bank_ref, counterparty_name=bank_name)

    def _inject_exact_duplicates(self) -> None:
        """TRD §11.4: 1% exact duplicates (suppressed by idempotency but still
        counted) and 0.05% same-key-different-amount (IDEMPOTENCY_CONFLICT)."""
        rng = self.rng["defect"]
        if not self.transactions:
            return
        for _ in range(int(len(self.transactions) * DEFECTS["exact_duplicate"])):
            duplicate = dict(rng.choice(self.transactions))
            # Counted once, as a duplicate, whatever its source row carried.
            duplicate["_defects"] = ["exact_duplicate"]
            self.transactions.append(duplicate)
        for _ in range(max(1, int(len(self.transactions) * DEFECTS["idempotency_conflict"]))):
            conflict = dict(rng.choice(self.transactions))
            conflict["amount_original"] = _money(float(conflict["amount_original"] or 1000) + 77_000)
            conflict["_defects"] = ["idempotency_conflict"]
            self.transactions.append(conflict)

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

    SPILL_ORDER = ("RETAIL_NORMAL", "BUSINESS_NORMAL")

    def _pool(self, cohort: str) -> list[Party]:
        if cohort not in self._pools:
            members = [p for p in self.parties.values() if p.cohort == cohort and p.accounts]
            self.rng["scenario"].shuffle(members)
            self._pools[cohort] = members
        return self._pools[cohort]

    def _take(self, count: int, *, primary: str, individuals_only: bool = False) -> list[Party]:
        """Claim `count` distinct parties for a scenario: from the `primary`
        cohort first, spilling to ordinary retail and then business parties
        when it runs out.

        Every claimed party carries exactly one scenario. A party holding both
        a positive and an edge label makes the edge label a lie, and P05 carves
        its dormancy gap by deleting history, which would silently remove
        another scenario's transactions. Control-clean parties are never
        claimed, so a scenario can never land on the control cohort.
        """
        taken: list[Party] = []
        for cohort in (primary, *self.SPILL_ORDER):
            pool = self._pool(cohort)
            i = len(pool) - 1
            while i >= 0 and len(taken) < count:
                party = pool[i]
                if party.source_id in self._claimed:
                    pool.pop(i)  # claimed elsewhere (P10 takes businesses via their merchant)
                elif not (individuals_only and party.is_business):
                    pool.pop(i)
                    self._claimed.add(party.source_id)
                    self._placement[primary][cohort] += 1
                    taken.append(party)
                i -= 1
            if len(taken) == count:
                break
        return taken

    def inject_scenarios(self) -> None:
        rng = self.rng["scenario"]
        self._customer_rows = {c["source_customer_id"]: c for c in self.customers}
        self._history = self._amount_history()
        builders = {
            "P01": self._inject_p01, "P02": self._inject_p02, "P03": self._inject_p03,
            "P04": self._inject_p04, "P05": self._inject_p05, "P06": self._inject_p06,
            "P07": self._inject_p07, "P08": self._inject_p08, "P09": self._inject_p09,
            "P10": self._inject_p10, "P11": self._inject_p11, "P12": self._inject_p12,
            "ATO": self._inject_ato,
        }
        for pattern, kinds in SCENARIO_PLAN.items():
            count = self._scenario_count(pattern, "positives", kinds["positives"])
            for n in range(1, count + 1):
                self._as_scenario(f"{pattern}-POS-{n:04d}", lambda sid, p=pattern: builders[p](rng, sid))

        # Behavioural look-alikes and exact-threshold boundary cases. Both are
        # EDGE_CASE: legitimate activity that a badly tuned rule would catch.
        for pattern, kinds in SCENARIO_PLAN.items():
            for n in range(1, self._scenario_count(pattern, "edge", kinds["edge"]) + 1):
                if pattern == "ATO":
                    self._as_scenario(f"ATO-EDGE-{n:04d}", lambda sid, k=n - 1: self._inject_ato_edge(rng, sid, k))
                else:
                    self._as_scenario(f"{pattern}-EDGE-{n:04d}", lambda sid, p=pattern: self._inject_edge(rng, p, sid))
            if "boundary" in kinds:
                for n in range(1, self._scenario_count(pattern, "boundary", kinds["boundary"]) + 1):
                    self._as_scenario(f"{pattern}-BOUND-{n:04d}",
                                      lambda sid, p=pattern: self._inject_boundary(rng, p, sid))

        self._apply_gaps()
        self.label_transactions.sort(key=lambda r: (r["scenario_id"], r["source_transaction_reference"]))

    def _as_scenario(self, scenario_id: str, build) -> None:
        """Run one scenario builder with its rows recorded against its id."""
        self._scenario = scenario_id
        try:
            build(scenario_id)
        finally:
            self._scenario = None

    def _amount_history(self) -> dict[str, dict[str, list[float]]]:
        """Each party's own background amounts, read before any scenario is
        placed: an ATO drain is sized against what that customer usually
        spends, and a look-alike spends like its owner does."""
        owner = {a["source_account_id"]: a["owner_source_id"] for a in self.accounts}
        history: dict[str, dict[str, list[float]]] = {}
        for row in self.transactions:
            party = owner.get(row["source_account_id"])
            if party is None:
                continue
            try:
                amount = float(row["amount_original"])
            except ValueError:
                continue
            if amount <= 0:
                continue
            entry = history.setdefault(party, {"all": [], "out": []})
            entry["all"].append(amount)
            if row["direction"] == "OUT":
                entry["out"].append(amount)
        return history

    def _carve_gap(self, party: Party, start: date, end: date) -> None:
        """No background activity for `party` in [start, end): dormancy derived
        from transaction history (FRD §8.5), carved after every scenario is placed."""
        self._gaps.append((set(party.accounts), start.isoformat(), end.isoformat()))

    def _apply_gaps(self) -> None:
        if not self._gaps:
            return
        scenario_rows = {r["source_transaction_reference"] for r in self.label_transactions}
        by_account: dict[str, list[tuple[str, str]]] = {}
        for accounts, start, end in self._gaps:
            for account in accounts:
                by_account.setdefault(account, []).append((start, end))

        def carved(row: dict[str, Any]) -> bool:
            spans = by_account.get(row["source_account_id"])
            if not spans or row["source_transaction_reference"] in scenario_rows:
                return False
            return any(start <= row["business_date"] < end for start, end in spans)

        self.transactions = [row for row in self.transactions if not carved(row)]

    def _scenario_count(self, pattern: str, kind: str, full_count: int) -> int:
        """How many scenarios of one kind to inject at this scale.

        The presets keep their original minimum of one. The custom scale floors
        every count at min_per_pattern so each pattern keeps enough samples to
        test, and records wherever the floor overrode pure proportion.
        """
        if self.target_transactions is None:
            count = _scaled(full_count, self.factor, 1)
        else:
            proportional = round(full_count * self.factor)
            count = max(self.min_per_pattern, proportional)
            if count != proportional:
                self._floor_adjustments.append({
                    "pattern": pattern, "kind": kind, "full_profile": full_count,
                    "proportional": proportional, "applied": count,
                })
        self._requested_scenarios.setdefault(pattern, {})[kind] = count
        return count

    def calibrate_to_target(self) -> None:
        """Land the transaction count exactly on target_transactions (B1).

        Background generation undershoots by ~2.5% — integer division per
        party, control parties at half volume, the gauss floor — so the
        difference is made up, or if generation ever overshoots, removed, using
        only ordinary parties that carry no label and no scenario. Labelled
        entities are never touched, so P05's dormancy gaps, the look-alike
        spacing and the control cohort stay exactly as labelled.

        Added rows use the same background shape and the same per-row defect
        draw, and exact duplicates and idempotency conflicts are added at their
        declared rates, so defect rates hold across the whole file. Removal
        takes a uniform sample of single-copy rows, which leaves per-row
        defect rates unchanged.
        """
        # Its own stream, created only here, so the preset profiles' seeds.json
        # does not change.
        rng = self.rng.setdefault("calibrate", random.Random(_seed_for(self.master_seed, "calibrate")))
        eligible = [
            p for p in self.parties.values()
            if p.accounts and p.source_id not in self._claimed and p.cohort not in ("CONTROL_CLEAN", "ER_TEST")
        ]
        generated = len(self.transactions)
        delta = self.target_transactions - generated
        if delta > 0:
            if not eligible:
                raise RuntimeError("no unlabelled parties left to carry calibration rows")
            duplicates = round(delta * DEFECTS["exact_duplicate"])
            conflicts = round(delta * DEFECTS["idempotency_conflict"])
            fresh = [self._emit_background(rng, rng.choice(eligible)) for _ in range(delta - duplicates - conflicts)]
            for _ in range(duplicates):
                duplicate = dict(rng.choice(fresh))
                duplicate["_defects"] = ["exact_duplicate"]
                self.transactions.append(duplicate)
            for _ in range(conflicts):
                conflict = dict(rng.choice(fresh))
                conflict["amount_original"] = _money(float(conflict["amount_original"] or 1000) + 77_000)
                conflict["_defects"] = ["idempotency_conflict"]
                self.transactions.append(conflict)
        elif delta < 0:
            eligible_ids = {p.source_id for p in eligible}
            owner = {a["source_account_id"]: a["owner_source_id"] for a in self.accounts}
            copies = Counter(r["source_transaction_reference"] for r in self.transactions)
            # A weekly sweep is derived from the week's takings, so it stays.
            candidates = [
                i for i, r in enumerate(self.transactions)
                if owner.get(r["source_account_id"]) in eligible_ids and copies[r["source_transaction_reference"]] == 1
                and r["transaction_type"] != "SETTLEMENT"
            ]
            drop = set(rng.sample(candidates, min(-delta, len(candidates))))
            self.transactions = [r for i, r in enumerate(self.transactions) if i not in drop]
        self._calibration = {
            "generated_before_calibration": generated,
            "added": max(0, len(self.transactions) - generated),
            "removed": max(0, generated - len(self.transactions)),
            "final": len(self.transactions),
        }
        if len(self.transactions) != self.target_transactions:
            # Only reachable below MIN_TARGET_TRANSACTIONS, where the scenario
            # floors leave too few unlabelled parties to add to or trim from.
            raise RuntimeError(
                f"calibration could not land on {self.target_transactions:,} transactions "
                f"(got {len(self.transactions):,}): too few unlabelled parties at this scale"
            )

    def _scale_report(self) -> dict[str, Any]:
        # A scenario is "placed" when its label was written; P09 writes one
        # label row per participating party, hence the set of scenario ids.
        markers = {"positives": "-POS-", "edge": "-EDGE-", "boundary": "-BOUND-"}
        placed_ids = {label["scenario_id"] for label in self.labels}
        scenarios = {
            pattern: {
                kind: {
                    "requested": requested,
                    "placed": sum(1 for sid in placed_ids if sid.startswith(f"{pattern}{markers[kind]}")),
                }
                for kind, requested in kinds.items()
            }
            for pattern, kinds in self._requested_scenarios.items()
        }
        return {
            "target_transactions": self.target_transactions,
            "scale_factor": round(self.factor, 6),
            "derived_from": "TRD §11.1 full-profile targets x (target_transactions / 420,000)",
            "entity_targets": {name: round(full * self.factor) for name, full in FULL_TARGETS.items()},
            "min_per_pattern": self.min_per_pattern,
            "floor_adjustments": self._floor_adjustments,
            "scenarios": scenarios,
            "calibration": self._calibration,
            # TRD §11.6 seeds cases through the normal service path so their
            # audit trail is authentic, and cases are not part of the §6.1
            # source contract, so this generator does not produce them.
            "not_generated": {"cases": {"proportional_target": round(180 * self.factor),
                                        "reason": "TRD §11.6: seeded through the service layer, not source data"}},
        }

    def _scale_label(self) -> str:
        if self.target_transactions is None:
            return ""
        return f" (target {self.target_transactions:,} transactions, scale factor {self.factor:.6f})"

    def _pick_day(self, rng: random.Random, first: date, last: date) -> date:
        """A day in [first, last], weighted like background days (paydays busier,
        weekends quieter). 1.1.0 drew scenario days uniformly, so scenarios fell
        on paydays half as often as everything else."""
        days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
        return rng.choices(days, weights=[self._day_weight(d) for d in days])[0]

    def _window_start(self, rng: random.Random, span_days: int) -> date:
        latest = self.reference_date - timedelta(days=span_days + 1)
        earliest = self.period_start + timedelta(days=60)  # leave a baseline behind it
        if latest <= earliest:
            latest = earliest + timedelta(days=1)
        return self._pick_day(rng, earliest, latest - timedelta(days=1))

    def _inject_p01(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        day = self._window_start(rng, 1)
        self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                   amount=rng.uniform(2_000_000_000, 8_000_000_000), direction="OUT", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", device_id=self._device_at(rng, party, day),
                   defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P01", party.source_id, day, day,
                    "Single transfer far above the individual threshold band")

    def _inject_p02(self, rng: random.Random, scenario_id: str) -> None:
        """Deliberately built to satisfy the implemented detector: 3-5
        transactions inside [350M, 500M) within 7 days, aggregate >= 500M."""
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 7)
        count = rng.randrange(3, 6)
        for n in range(count):
            day = self._pick_day(rng, start, start + timedelta(days=5))
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(350_000_000, 499_000_000), direction="IN",
                       channel=rng.choice(("CASH", "TRANSFER")), transaction_type="CASH_DEPOSIT",
                       device_id=self._device_at(rng, party, day), defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P02", party.source_id, start,
                    start + timedelta(days=6), f"{count} deposits in band within a 7-day window")

    def _inject_p03(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        day = self._window_start(rng, 1)
        amount = rng.uniform(200_000_000, 900_000_000)
        account = rng.choice(party.accounts)
        # Any hour, like every other row (1.1.0 fixed the arrival at 09:xx).
        arrival = self._random_moment(rng, day)
        departure = arrival + timedelta(hours=rng.randrange(1, 6))
        self._emit(account_id=account, when=arrival, amount=amount, direction="IN", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", device_id=self._device_at(rng, party, day), defects="soft")
        self._emit(account_id=account, when=departure,
                   amount=amount * rng.uniform(0.94, 0.99), direction="OUT", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", device_id=self._device_at(rng, party, departure.date()),
                   defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P03", party.source_id, day, departure.date(),
                    "Funds in and substantially out again within hours")

    def _inject_p04(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 3)
        for _ in range(rng.randrange(40, 90)):
            day = self._pick_day(rng, start, start + timedelta(days=2))
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(50_000, 2_500_000), direction="OUT", channel="QRIS",
                       transaction_type="MERCHANT_PAYMENT", device_id=self._device_at(rng, party, day),
                       merchant_id=self._merchant_for(party, day), defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P04", party.source_id, start,
                    start + timedelta(days=3), "Transaction count far above the entity's own baseline")

    def _inject_p05(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        # Dormancy is derived from transaction history (FRD E04), so the
        # scenario is a gap in activity followed by something material.
        day = self._pick_day(rng, self.reference_date - timedelta(days=39), self.reference_date - timedelta(days=5))
        # FRD §8.5 DORMANCY_DAYS >= 90, leaving at least 30 days of history in
        # view (1.2.0's 150 days erased nearly all of it, so even a regular
        # counterparty looked new).
        quiet = self._quiet_days(rng, day)
        gap_start = (day - timedelta(days=quiet)).isoformat()
        # Dormancy is per account and needs activity before it: the party's
        # busiest account before the gap.
        account = self._ensure_history(rng, party, date.fromisoformat(gap_start))
        self.transactions = [
            t for t in self.transactions
            if not (t["source_account_id"] == account and gap_start <= t["business_date"] < day.isoformat())
        ]
        # The credit's counterparty comes from the party's book like any other
        # row's. That it often looks unfamiliar is what dormancy does inside a
        # six-month window: the account's history before the gap is short.
        self._emit(account_id=account, when=self._random_moment(rng, day),
                   amount=rng.uniform(400_000_000, 1_500_000_000), direction="IN", channel="TRANSFER",
                   transaction_type="P2P_TRANSFER", device_id=self._device_at(rng, party, day), defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P05", party.source_id,
                    day - timedelta(days=quiet), day, f"{quiet} dormant days, then a material credit")

    def _inject_p06(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 14)
        amount = rng.choice((50_000_000, 100_000_000, 250_000_000))
        for n in range(rng.randrange(6, 12)):
            day = self._pick_day(rng, start, start + timedelta(days=13))
            self._emit(account_id=rng.choice(party.accounts),
                       when=self._random_moment(rng, day), amount=amount,
                       direction="OUT", channel="TRANSFER", transaction_type="P2P_TRANSFER",
                       device_id=self._device_at(rng, party, day), defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P06", party.source_id, start,
                    start + timedelta(days=13), f"Repeated identical round amount {amount:,}")

    def _inject_p07(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 35)
        amount = rng.uniform(5_000_000, 15_000_000)
        for week in range(5):
            amount *= rng.uniform(2.0, 3.0)
            for _ in range(rng.randrange(2, 5)):
                day = self._pick_day(rng, start + timedelta(days=week * 7), start + timedelta(days=week * 7 + 6))
                # Around the week's level, not the same amount every time (1.2.0
                # repeated one amount, a signal P07's definition does not have).
                self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                           amount=amount * rng.uniform(0.9, 1.1), direction="OUT", channel="TRANSFER",
                           transaction_type="P2P_TRANSFER", device_id=self._device_at(rng, party, day),
                           defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P07", party.source_id, start,
                    start + timedelta(days=35), "Weekly value stepping up by 2-3x")

    def _inject_p08(self, rng: random.Random, scenario_id: str) -> None:
        party = self._take(1, primary="INJECTED_CANDIDATE")
        if not party:
            return
        party = party[0]
        start = self._window_start(rng, 20)
        country = rng.choice(HIGH_RISK_COUNTRIES)
        # A few beneficiaries abroad, paid repeatedly, not a new one each time.
        beneficiaries = [self._one_off() for _ in range(rng.randrange(1, 4))]
        for _ in range(rng.randrange(8, 18)):
            beneficiary_ref, beneficiary_name = rng.choice(beneficiaries)
            day = self._pick_day(rng, start, start + timedelta(days=19))
            self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                       amount=rng.uniform(80_000_000, 400_000_000), direction="OUT", channel="REMITTANCE",
                       transaction_type="INBOUND_REMITTANCE", counterparty_country=country,
                       counterparty_ref=beneficiary_ref, counterparty_name=beneficiary_name,
                       device_id=self._device_at(rng, party, day), defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P08", party.source_id, start,
                    start + timedelta(days=20), f"Exposure concentrated on listed geography {country}")

    def _inject_p09(self, rng: random.Random, scenario_id: str) -> None:
        parties = self._take(rng.randrange(4, 8), primary="INJECTED_CANDIDATE")
        if len(parties) < 3:
            return
        start = self._window_start(rng, 14)
        # The ring's own device, first seen like any other device (well before
        # the period). 1.2.0 borrowed an uninvolved customer's device, which
        # made that customer look shared too.
        shared = self._new_device(self.rng["device"])
        for party in parties:
            for _ in range(rng.randrange(3, 7)):
                day = self._pick_day(rng, start, start + timedelta(days=13))
                self._emit(account_id=rng.choice(party.accounts), when=self._random_moment(rng, day),
                           amount=rng.uniform(1_000_000, 40_000_000), direction="OUT", channel="TRANSFER",
                           transaction_type="P2P_TRANSFER", device_id=shared, defects="soft")
        for party in parties:
            self._label(scenario_id, "INJECTED_POSITIVE", "P09", party.source_id, start,
                        start + timedelta(days=14), f"Device {shared} shared by {len(parties)} unrelated entities")

    def _inject_p10(self, rng: random.Random, scenario_id: str) -> None:
        candidates = [
            m for m in self.merchants
            if m["source_business_id"] not in self._claimed and self.parties[m["source_business_id"]].accounts
        ]
        if not candidates:
            return
        merchant = rng.choice(candidates)
        business = self.parties[merchant["source_business_id"]]
        self._claimed.add(business.source_id)
        self._placement["INJECTED_CANDIDATE"]["BUSINESS_NORMAL (P10 merchant)"] += 1
        start = self._window_start(rng, 25)
        # Ticket sizes an order of magnitude away from what the MCC implies.
        for _ in range(rng.randrange(20, 45)):
            day = self._pick_day(rng, start, start + timedelta(days=24))
            self._emit(account_id=rng.choice(business.accounts),
                       when=self._merchant_moment(rng, merchant["source_merchant_id"], day),
                       amount=rng.uniform(25_000_000, 120_000_000), direction="IN", channel="QRIS",
                       transaction_type="MERCHANT_PAYMENT", merchant_id=merchant["source_merchant_id"],
                       device_id=self._device_at(rng, business, day), defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P10", merchant["source_business_id"], start,
                    start + timedelta(days=25), f"Ticket size inconsistent with MCC {merchant['mcc']}")

    def _inject_p11(self, rng: random.Random, scenario_id: str) -> None:
        senders = self._take(rng.randrange(8, 16), primary="INJECTED_CANDIDATE")
        collector = self._take(1, primary="INJECTED_CANDIDATE")
        if len(senders) < 5 or not collector:
            return
        collector = collector[0]
        target_account = rng.choice(collector.accounts)
        start = self._window_start(rng, 5)
        # The funnel's own device, as for P09.
        shared_device = self._new_device(self.rng["device"])
        for sender in senders:
            day = self._pick_day(rng, start, start + timedelta(days=4))
            amount = rng.uniform(20_000_000, 90_000_000)
            self._emit(account_id=rng.choice(sender.accounts), when=self._random_moment(rng, day), amount=amount,
                       direction="OUT", channel="TRANSFER", transaction_type="P2P_TRANSFER",
                       device_id=shared_device, counterparty_ref=target_account,
                       counterparty_name=collector.name, defects="soft")
            self._emit(account_id=target_account, when=self._random_moment(rng, day), amount=amount,
                       direction="IN", channel="TRANSFER", transaction_type="P2P_TRANSFER",
                       device_id=shared_device, counterparty_ref=rng.choice(sender.accounts),
                       counterparty_name=sender.name, defects="soft")
        self._label(scenario_id, "INJECTED_POSITIVE", "P11", collector.source_id, start,
                    start + timedelta(days=5), f"{len(senders)}-to-1 convergence with device overlap")

    def _inject_p12(self, rng: random.Random, scenario_id: str) -> None:
        if not self.watchlist or not self.customers:
            return
        listed = rng.choice(self.watchlist)
        taken = self._take(1, primary="INJECTED_CANDIDATE", individuals_only=True)
        if not taken:
            return
        customer = self._customer_rows[taken[0].source_id]
        # A near-name, never an exact copy: screening stops at potential match
        # (FRD §3.5) and the reviewer is the one who decides.
        customer["full_name"] = listed["primary_name"].replace("i", "y", 1)
        taken[0].name = customer["full_name"]
        # A renamed record is no longer a truncated one.
        customer["_defects"] = [d for d in customer["_defects"] if d != "truncated_name"]
        self._label(scenario_id, "INJECTED_POSITIVE", "P12", customer["source_customer_id"],
                    self.period_start, self.reference_date,
                    f"Name close to list record {listed['list_record_id']} ({listed['list_type']})")

    def _row(self, rng: random.Random, party: Party, when: datetime, amount: float, direction: str,
             channel: str, ttype: str, **extra: Any) -> dict[str, Any]:
        """A scenario row on the party's own device, with the soft defects."""
        device = extra.pop("device", None) or self._device_at(rng, party, when.date())
        return self._emit(account_id=rng.choice(party.accounts), when=when, amount=amount, direction=direction,
                          channel=channel, transaction_type=ttype, device_id=device, defects="soft", **extra)

    def _paydays(self, rng: random.Random, months: int) -> list[date]:
        """One company's payday in `months` consecutive months, late enough to
        leave a baseline: its own day of the month, the Friday before when it
        falls on a weekend."""
        pay_day = rng.choice(PAYROLL_DAYS)
        dates = []
        year, month = self.period_start.year, self.period_start.month
        while date(year, month, 1) <= self.reference_date:
            day = date(year, month, pay_day)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            if (day - self.period_start).days >= 60 and day <= self.reference_date - timedelta(days=3):
                dates.append(day)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        if len(dates) < months:
            first = self._window_start(rng, 31 * months)
            return [first + timedelta(days=30 * n) for n in range(months)]
        first = rng.randrange(len(dates) - months + 1)
        return dates[first:first + months]

    def _inject_edge(self, rng: random.Random, pattern: str, scenario_id: str) -> None:
        """Legitimate activity that resembles `pattern` (TRD §11.2: "perilaku sah
        yang menyerupai suatu pattern"), built from the TRD §11.2 examples and
        the FRD §8 lists of expected false positives. If these were trivially
        separable, the false-positive discussion would be meaningless.

        1.3.0 built only five of them this way; the other seven were six
        ordinary transfers that resembled nothing."""
        if pattern == "P10":
            self._edge_p10(rng, scenario_id)
            return
        size = rng.randrange(3, 5) if pattern == "P09" else 1
        parties = self._take(size, primary="EDGE_AMBIGUOUS", individuals_only=True)
        if len(parties) < size:
            return
        party = parties[0]
        if pattern == "P01":
            # FRD §8.1: a vehicle down payment or an annual bonus, under the
            # individual threshold but far above the customer's own usual.
            day = self._window_start(rng, 1)
            bonus = rng.random() < 0.5
            self._row(rng, party, self._random_moment(rng, day), rng.uniform(60e6, 95e6),
                      "IN" if bonus else "OUT", "TRANSFER", "P2P_TRANSFER")
            note, start, end = ("Annual bonus" if bonus else "Vehicle down payment") + \
                ", under the individual threshold", day, day
        elif pattern == "P02":
            # TRD §11.2: an arisan collector's cash deposits, in band but always
            # spread past the 7-day window (spacing 9, one-day slot: >= 8 days apart).
            start = self._window_start(rng, 45)
            for n in range(5):
                target = start + timedelta(days=9 * n)
                day = self._pick_day(rng, target, target + timedelta(days=1))
                self._row(rng, party, self._random_moment(rng, day), 380_000_000 * rng.uniform(0.8, 1.2),
                          "IN", "CASH", "CASH_DEPOSIT")
            note, end = "Arisan collector: in-band deposits, but always spread past the 7-day window", \
                start + timedelta(days=45)
        elif pattern == "P03":
            # FRD §8.3: a wallet used as a pipe, salary in and bills and savings
            # out the same day, below the pass-through minimum credit.
            paydays = self._paydays(rng, 2)
            start = paydays[0]
            for day in paydays:
                salary = rng.uniform(20e6, 80e6)  # a senior salary: about half reach MIN_INBOUND_AMOUNT
                moments = self._burst(rng, day, rng.randrange(4, 7), spread_hours=6)
                self._row(rng, party, moments[0], salary, "IN", "TRANSFER", "P2P_TRANSFER")
                shares = [rng.random() for _ in moments[1:]]
                out_total = salary * rng.uniform(0.85, 0.95)
                for when, share in zip(moments[1:], shares):
                    ttype, channel = rng.choice((("BILL_PAYMENT", "PAYMENT"), ("P2P_TRANSFER", "TRANSFER")))
                    self._row(rng, party, when, out_total * share / sum(shares), "OUT", channel, ttype)
            note, end = "Salary in, bills and savings out the same day, every month", paydays[-1] + timedelta(days=1)
        elif pattern == "P04":
            # TRD §11.2: a payroll disburser on payday, a burst of transfers to
            # the same staff every month.
            paydays = self._paydays(rng, 2)
            start = paydays[0]
            staff = [self._one_off() for _ in range(rng.randrange(15, 41))]
            for day in paydays:
                for when, (ref, name) in zip(self._burst(rng, day, len(staff), spread_hours=3), staff):
                    self._row(rng, party, when, rng.uniform(3e6, 8e6), "OUT", "TRANSFER", "P2P_TRANSFER",
                              counterparty_ref=ref, counterparty_name=name)
            note, end = f"Payroll disburser on payday: {len(staff)} staff paid within hours, every month", \
                paydays[-1] + timedelta(days=1)
        elif pattern == "P05":
            # FRD §8.5: back from working abroad, the account quiet for months,
            # then a modest credit and a payment or two (under the reactivation minimum).
            start = self._takeover_day(rng)
            quiet = self._quiet_days(rng, start)
            self._ensure_history(rng, party, start - timedelta(days=quiet))
            self._carve_gap(party, start - timedelta(days=quiet), start + timedelta(days=4))
            moments = sorted(self._random_moment(rng, start + timedelta(days=n)) for n in range(rng.randrange(2, 4)))
            self._row(rng, party, moments[0], rng.uniform(10e6, 40e6), "IN", "TRANSFER", "P2P_TRANSFER")
            for when in moments[1:]:
                channel, ttype, merchant = self._ordinary_payment(rng, party, when.date())
                self._row(rng, party, when, self._usual_amount(rng, party), "OUT", channel, ttype, merchant_id=merchant)
            note, end = f"Back from working abroad after {quiet} quiet days: savings sent home, ordinary spending", \
                moments[-1].date()
        elif pattern == "P06":
            # TRD §11.2: an agent kiosk topping up its float in round amounts
            # (FRD §8.6 lists round top-ups as expected false positives).
            start = self._window_start(rng, 16)
            for n in range(8):
                target = start + timedelta(days=2 * n)
                day = self._pick_day(rng, target, target + timedelta(days=1))
                self._row(rng, party, self._random_moment(rng, day), round(20e6 * rng.uniform(0.8, 1.2) / 1e6) * 1e6,
                          "IN", "AGENT", "WALLET_TOPUP")
            note, end = "Agent kiosk float top-ups in round amounts", start + timedelta(days=16)
        elif pattern == "P07":
            # TRD §11.2: a seasonal trader, three weeks of sales far above its
            # own usual week, from many buyers (Ramadan, year-end).
            start = self._window_start(rng, 21)
            for week in range(3):
                for _ in range(rng.randrange(20, 36)):
                    day = self._pick_day(rng, start + timedelta(days=7 * week), start + timedelta(days=7 * week + 6))
                    self._row(rng, party, self._random_moment(rng, day), rng.uniform(2_000_000, 6_000_000), "IN",
                              "TRANSFER", "P2P_TRANSFER", counterparty="new" if rng.random() < 0.7 else "usual")
            note, end = "Seasonal trader: three weeks of sales from many buyers", start + timedelta(days=21)
        elif pattern == "P08":
            # TRD §11.2: a student abroad, family support from a normal
            # corridor, the same sender every time.
            start = self._window_start(rng, 84)
            family = self._one_off()
            for n in range(6):
                target = start + timedelta(days=14 * n)
                day = self._pick_day(rng, target, target + timedelta(days=13))
                self._row(rng, party, self._random_moment(rng, day), 15e6 * rng.uniform(0.8, 1.2), "IN",
                          "REMITTANCE", "INBOUND_REMITTANCE", counterparty_country=rng.choice(NORMAL_CORRIDORS),
                          counterparty_ref=family[0], counterparty_name=family[1])
            note, end = "Cross-border student receiving family support", start + timedelta(days=84)
        elif pattern == "P09":
            # TRD §11.2 / FRD §8.9: a family sharing one handset, members living
            # at one address, each paying for ordinary things on it.
            start = self._window_start(rng, 35)
            handset = self._new_device(self.rng["device"])
            home = self._customer_rows[party.source_id]
            for member in parties:
                row = self._customer_rows[member.source_id]
                for field_name in ("address_line", "city", "province", "postcode"):
                    row[field_name] = home[field_name]
                for _ in range(rng.randrange(4, 9)):
                    day = self._pick_day(rng, start, start + timedelta(days=34))
                    when = self._random_moment(rng, day)
                    channel, ttype, merchant = self._ordinary_payment(rng, member, day)
                    self._row(rng, member, when, self._usual_amount(rng, member), "OUT", channel, ttype,
                              merchant_id=merchant, device=handset)
            end = start + timedelta(days=35)
            for member in parties:
                self._label(scenario_id, "EDGE_CASE", "P09", member.source_id, start, end,
                            f"Family of {len(parties)} at one address sharing one handset")
            return
        elif pattern == "P11":
            # TRD §11.2: a school fee account, many payers paying the same fee,
            # the same names every month, spread over days (no compression) -
            # it differs from a funnel in compression, device overlap and
            # amount uniformity, as §11.2 says it should.
            start = self._window_start(rng, 45)
            payers = [self._one_off() for _ in range(rng.randrange(30, 61))]
            fee = round(rng.uniform(4e6, 8e6) / 50_000) * 50_000
            for month in range(2):
                for ref, name in payers:
                    day = self._pick_day(rng, start + timedelta(days=30 * month),
                                         start + timedelta(days=30 * month + 11))
                    self._row(rng, party, self._random_moment(rng, day), fee, "IN", "TRANSFER", "P2P_TRANSFER",
                              counterparty_ref=ref, counterparty_name=name)
            note, end = f"School fee account: {len(payers)} payers, the same fee, the same names every month", \
                start + timedelta(days=42)
        elif pattern == "P12":
            # FRD §8.12: a very common given name shared with a list record,
            # the family name different. No transactions of its own.
            listed = rng.choice(self.watchlist)
            tokens = [t for t in listed["primary_name"].split() if not t.endswith(".")]
            others = [f for f in FAMILY if f != tokens[-1]]
            customer = self._customer_rows[party.source_id]
            customer["full_name"] = f"{tokens[0]} {rng.choice(others)}"
            customer["_defects"] = [d for d in customer["_defects"] if d != "truncated_name"]
            party.name = customer["full_name"]
            note, start, end = f"Shares a common given name with list record {listed['list_record_id']}", \
                self.period_start, self.reference_date
        else:
            raise ValueError(f"no look-alike construction for {pattern}")
        self._label(scenario_id, "EDGE_CASE", pattern, party.source_id, start, end, note)

    def _edge_p10(self, rng: random.Random, scenario_id: str) -> None:
        """FRD §8.10: a B2B supplier that really does serve a handful of large
        clients, at tickets its category explains."""
        candidates = [m for m in self.merchants
                      if m["source_business_id"] not in self._claimed and self.parties[m["source_business_id"]].accounts]
        if not candidates:
            return
        merchant = rng.choice(candidates)
        business = self.parties[merchant["source_business_id"]]
        self._claimed.add(business.source_id)
        self._placement["EDGE_AMBIGUOUS"]["BUSINESS_NORMAL (P10 merchant)"] += 1
        start = self._window_start(rng, 25)
        clients = [self._one_off() for _ in range(rng.randrange(3, 6))]
        typical = self._merchant_ticket[merchant["source_merchant_id"]]
        for _ in range(rng.randrange(20, 41)):
            day = self._pick_day(rng, start, start + timedelta(days=24))
            ref, name = rng.choice(clients)
            self._row(rng, business, self._merchant_moment(rng, merchant["source_merchant_id"], day),
                      typical * rng.uniform(1.5, 2.5), "IN", "QRIS",
                      "MERCHANT_PAYMENT", merchant_id=merchant["source_merchant_id"],
                      counterparty_ref=ref, counterparty_name=name)
        self._label(scenario_id, "EDGE_CASE", "P10", business.source_id, start, start + timedelta(days=25),
                    f"B2B supplier with {len(clients)} large clients, tickets consistent with MCC {merchant['mcc']}")

    def _inject_boundary(self, rng: random.Random, pattern: str, scenario_id: str) -> None:
        """Exactly on the pattern's own FRD §8 threshold, on the side that must
        not fire. 1.3.0 used three 100,000,000 deposits for every pattern but
        P02, which sits on none of their thresholds (P07's rule fires on it).

        The case is negative for its own pattern only. Where the party's own
        background could tip it over (one more payment in the same 24 hours,
        one more sender in the same week) that background is carved away.
        Other rules may still fire on it, as they would on a real customer:
        a IDR 60M credit is large for a retail customer (P01's relative
        condition), a IDR 500M deposit exceeds P01's absolute threshold."""
        size = 3 if pattern == "P09" else 1
        parties = self._take(size, primary="EDGE_AMBIGUOUS", individuals_only=True)
        if len(parties) < size:
            return
        party = parties[0]
        start = self._window_start(rng, 7)
        if pattern == "P01" and (start - self.period_start).days < 100:
            start = self._pick_day(rng, self.period_start + timedelta(days=100), self.reference_date - timedelta(days=8))
        moment = self._random_moment(rng, start)
        end = start + timedelta(days=7)
        if pattern == "P01":
            # FRD TD-P01-NEG-02: fewer than MIN_HISTORY_TXNS in the 90-day
            # baseline, so the relative condition is not evaluated either.
            self._carve_gap(party, start - timedelta(days=90), start + timedelta(days=7))
            for offset in sorted(rng.sample(range(8, 89), 8), reverse=True):
                day = start - timedelta(days=offset)
                channel, ttype, merchant = self._ordinary_payment(rng, party, day)
                self._row(rng, party, self._random_moment(rng, day), self._usual_amount(rng, party), "OUT",
                          channel, ttype, merchant_id=merchant)
            self._row(rng, party, moment, 100_000_000.0, "OUT", "TRANSFER", "P2P_TRANSFER")
            note = ("One transfer of exactly IDR 100,000,000 (at, not above, the individual threshold) from a "
                    "customer with 8 transactions in 90 days, under MIN_HISTORY_TXNS 20 (FRD §8.1)")
        elif pattern == "P02":
            for n in range(3):
                day = start + timedelta(days=n)
                self._row(rng, party, self._random_moment(rng, day), 500_000_000.0, "IN", "CASH", "CASH_DEPOSIT")
            note = "Exactly at threshold (500,000,000): the threshold is exclusive, so not in band (FRD §8.2)"
        elif pattern == "P03":
            self._carve_gap(party, start, start + timedelta(days=2))
            credit = 60_000_000.0
            self._row(rng, party, moment, credit, "IN", "TRANSFER", "P2P_TRANSFER")
            self._row(rng, party, moment + timedelta(hours=3), credit * 0.79, "OUT", "TRANSFER", "P2P_TRANSFER")
            note = "79% of a IDR 60M credit out within hours: under PASSTHROUGH_RATIO 0.80 (FRD §8.3)"
        elif pattern == "P04":
            self._carve_gap(party, start - timedelta(days=1), start + timedelta(days=2))
            for when in self._burst(rng, start, 14, spread_hours=6):
                channel, ttype, merchant = self._ordinary_payment(rng, party, start)
                self._row(rng, party, when, self._usual_amount(rng, party), "OUT", channel, ttype, merchant_id=merchant)
            note = "Exactly 14 transactions within 24 hours: under MIN_COUNT_FLOOR 15 (FRD §8.4)"
        elif pattern == "P05":
            day = self._takeover_day(rng)
            last = day - timedelta(days=89)
            self._carve_gap(party, last + timedelta(days=1), day + timedelta(days=8))
            account = rng.choice(party.accounts)
            self._emit(account_id=account, when=self._random_moment(rng, last), amount=self._usual_amount(rng, party),
                       direction="OUT", channel="PAYMENT", transaction_type="BILL_PAYMENT",
                       device_id=self._device_at(rng, party, last), defects="soft")
            self._emit(account_id=account, when=self._random_moment(rng, day), amount=30_000_000.0, direction="IN",
                       channel="TRANSFER", transaction_type="P2P_TRANSFER", device_id=self._device_at(rng, party, day),
                       defects="soft")
            start, end = last, day + timedelta(days=7)
            note = "Account quiet for 89 days, then a IDR 30M credit: under DORMANCY_DAYS 90 (FRD §8.5)"
        elif pattern == "P06":
            for n in range(4):
                day = start + timedelta(days=5 * n)
                self._row(rng, party, self._random_moment(rng, day), 10_000_000.0, "OUT", "TRANSFER", "P2P_TRANSFER")
            note = "Exactly 4 round amounts of IDR 10M in 30 days: under MIN_COUNT 5 (FRD §8.6)"
        elif pattern == "P09":
            device = self._new_device(self.rng["device"])
            for member in parties:
                for _ in range(rng.randrange(3, 6)):
                    day = self._pick_day(rng, start, start + timedelta(days=13))
                    self._row(rng, member, self._random_moment(rng, day), rng.uniform(1e6, 10e6), "OUT", "TRANSFER",
                              "P2P_TRANSFER", device=device)
            for member in parties:
                self._label(scenario_id, "EDGE_CASE", "P09", member.source_id, start, start + timedelta(days=14),
                            "One device used by exactly 3 entities: under MIN_DISTINCT_ENTITIES 4 (FRD §8.9)")
            return
        elif pattern == "P11":
            self._carve_gap(party, start - timedelta(days=7), start + timedelta(days=8))
            for when in self._burst(rng, start, 7, spread_hours=20):
                ref, name = self._one_off()
                self._row(rng, party, when, rng.uniform(30e6, 50e6), "IN", "TRANSFER", "P2P_TRANSFER",
                          counterparty_ref=ref, counterparty_name=name)
            note = "Exactly 7 senders within 48 hours, over IDR 200M: under MIN_DISTINCT_COUNTERPARTIES 8 (FRD §8.11)"
        else:
            raise ValueError(f"no boundary construction for {pattern}")
        self._label(scenario_id, "EDGE_CASE", pattern, party.source_id, start, end, note)

    def _ensure_history(self, rng: random.Random, party: Party, gap_start: date) -> str:
        """The party's busiest account before `gap_start`, given two ordinary
        payments in the month before if it has no activity there: a dormancy
        has to follow something. A quiet customer in 1.4.0's heavy tail may
        have none, and would look new rather than dormant."""
        before = Counter(t["source_account_id"] for t in self.transactions
                         if t["source_account_id"] in party.accounts and t["business_date"] < gap_start.isoformat())
        account = max(party.accounts, key=lambda a: (before[a], -party.accounts.index(a)))
        if not before[account]:
            for offset in sorted(rng.sample(range(1, 30), 2), reverse=True):
                day = gap_start - timedelta(days=offset)
                channel, ttype, merchant = self._ordinary_payment(rng, party, day)
                self._emit(account_id=account, when=self._random_moment(rng, day),
                           amount=self._usual_amount(rng, party), direction="OUT", channel=channel,
                           transaction_type=ttype, merchant_id=merchant, device_id=self._device_at(rng, party, day),
                           defects="soft")
        return account

    def _takeover_day(self, rng: random.Random) -> date:
        """Late enough to leave a history, then the longest dormancy, before it."""
        earliest = self.period_start + timedelta(days=ATO_DORMANCY_DAYS[1] + 30)
        latest = self.reference_date - timedelta(days=2)
        if latest <= earliest:
            earliest = self.period_start + timedelta(days=ATO_DORMANCY_DAYS[0] + 30)
            latest = max(latest, earliest + timedelta(days=1))
        return self._pick_day(rng, earliest, latest - timedelta(days=1))

    def _quiet_days(self, rng: random.Random, day: date) -> int:
        """A dormancy that still leaves at least 30 days of history before it."""
        room = (day - self.period_start).days - 30
        return rng.randrange(ATO_DORMANCY_DAYS[0], max(ATO_DORMANCY_DAYS[0], min(ATO_DORMANCY_DAYS[1], room)) + 1)


    def _usual_amount(self, rng: random.Random, party: Party) -> float:
        """One of the party's own ordinary amounts, so a look-alike spends like
        its owner rather than like a scenario."""
        amounts = self._history.get(party.source_id, {}).get("all") or [rng.lognormvariate(11.2, 1.2)]
        return max(1000.0, rng.choice(amounts) * rng.uniform(0.9, 1.1))

    def _outgoing_p95(self, party: Party) -> float:
        amounts = sorted(self._history.get(party.source_id, {}).get("out") or
                         self._history.get(party.source_id, {}).get("all") or [1_000_000.0])
        return amounts[min(len(amounts) - 1, int(0.95 * len(amounts)))]

    def _ordinary_payment(self, rng: random.Random, party: Party, day: date) -> tuple[str, str, str]:
        """(channel, transaction_type, merchant) for an everyday outgoing payment,
        to the party's usual merchants and regulars."""
        channel, ttype = rng.choice((("QRIS", "MERCHANT_PAYMENT"), ("PAYMENT", "BILL_PAYMENT"),
                                     ("TRANSFER", "P2P_TRANSFER")))
        merchant = self._merchant_for(party, day) if ttype == "MERCHANT_PAYMENT" else ""
        return channel, ttype, merchant

    def _inject_ato(self, rng: random.Random, scenario_id: str) -> None:
        """Account takeover: a customer with an ordinary history goes quiet for
        90-150 days, then a device that customer has never used appears and
        drains the wallet in a burst of outgoing payments within a few hours."""
        party = self._take(1, primary="INJECTED_CANDIDATE", individuals_only=True)
        if not party:
            return
        party = party[0]
        takeover_day = self._takeover_day(rng)
        quiet = self._quiet_days(rng, takeover_day)
        self._ensure_history(rng, party, takeover_day - timedelta(days=quiet))
        self._carve_gap(party, takeover_day - timedelta(days=quiet), takeover_day + timedelta(days=2))
        device = self._fresh_device(takeover_day)
        usual = self._outgoing_p95(party)
        moments = self._burst(rng, takeover_day, rng.randrange(5, 16), spread_hours=6)
        for last in moments:
            channel, ttype = rng.choices((("TRANSFER", "P2P_TRANSFER"), ("AGENT", "CASH_WITHDRAWAL"),
                                          ("QRIS", "MERCHANT_PAYMENT")), weights=(60, 25, 15))[0]
            merchant = ""
            if ttype == "MERCHANT_PAYMENT" and self._merchant_ids:
                # A merchant the victim never used, if there is one.
                used = {merchant for merchant, _ in party.merchants}
                unused = [m for m in self._merchant_ids if m not in used]
                merchant = rng.choice(unused or self._merchant_ids)
            amount = usual * rng.uniform(2.0, 8.0)
            if amount < 500_000:
                # A floor for thin histories that does not land on a round number.
                amount = 500_000 * rng.uniform(1.0, 1.6)
            self._emit(account_id=rng.choice(party.accounts), when=last,
                       amount=amount, direction="OUT", channel=channel,
                       transaction_type=ttype, merchant_id=merchant, device_id=device, defects="soft",
                       # A merchant is paid through its own reference; anyone
                       # else is a recipient this customer never paid.
                       counterparty="usual" if merchant else "new")
        hours = (moments[-1] - moments[0]).total_seconds() / 3600
        self._label(scenario_id, "INJECTED_POSITIVE", "ATO", party.source_id, takeover_day, takeover_day,
                    f"{quiet} quiet days, then {len(moments)} outgoing payments within {hours:.1f} h "
                    f"from a device this customer had never used")

    def _inject_ato_edge(self, rng: random.Random, scenario_id: str, index: int) -> None:
        """Legitimate activity carrying one or two of the three takeover
        signals, never all three (see ATO_EDGE_KINDS)."""
        kind, needs_new_device, note = ATO_EDGE_KINDS[index % len(ATO_EDGE_KINDS)]
        party = self._take(1, primary="EDGE_AMBIGUOUS", individuals_only=True)
        if not party:
            return
        party = party[0]
        if kind == "NEW_PHONE_PAYDAY":
            start = self._window_start(rng, 2)
            paydays = [start + timedelta(days=n) for n in range(0, 40) if (start + timedelta(days=n)).day in (25, 26, 27)]
            day = next((d for d in paydays if d <= self.reference_date - timedelta(days=1)), start)
        else:
            day = self._takeover_day(rng)
        quiet = self._quiet_days(rng, day)
        if kind != "NEW_PHONE_PAYDAY":
            self._ensure_history(rng, party, day - timedelta(days=quiet))
            self._carve_gap(party, day - timedelta(days=quiet), day + timedelta(days=4))
        device = self._fresh_device(day) if needs_new_device else None
        if kind == "RETURNING_NEW_PHONE":
            # Spread over the following days, like someone easing back in.
            moments = sorted(self._random_moment(rng, day + timedelta(days=min(n, 3)))
                             for n in range(rng.randrange(2, 5)))
        elif kind == "NEW_PHONE_PAYDAY":
            moments = self._burst(rng, day, rng.randrange(5, 11), spread_hours=6)
        else:
            moments = self._burst(rng, day, rng.randrange(3, 7), spread_hours=10)
        last = moments[-1]
        for when in moments:
            channel, ttype, merchant = self._ordinary_payment(rng, party, when.date())
            self._emit(account_id=rng.choice(party.accounts), when=when, amount=self._usual_amount(rng, party),
                       direction="OUT", channel=channel, transaction_type=ttype, merchant_id=merchant,
                       device_id=device or self._device_at(rng, party, when.date()), defects="soft")
        self._label(scenario_id, "EDGE_CASE", "ATO", party.source_id, day, last.date(),
                    f"{kind}: {note.format(days=quiet)}")

    # --- output ---------------------------------------------------------------

    def _defect_counts(self) -> dict[str, int]:
        """Defects present in what is actually written, counted from the rows
        rather than tallied as they are injected: P05 deletes history to carve
        its dormancy gaps, and a running tally kept counting the defect rows it
        removed."""
        counts: Counter[str] = Counter()
        for row in (*self.customers, *self.transactions):
            counts.update(row.get("_defects", ()))
        counts["placeholder_address"] = sum(
            1 for row in (*self.customers, *self.businesses) if row["address_line"] in PLACEHOLDER_ADDRESSES
        )
        owned = {bo["source_business_id"] for bo in self.beneficial_owners}
        counts["missing_bo"] = sum(1 for b in self.businesses if b["source_business_id"] not in owned)
        return {name: counts.get(name, 0) for name in DEFECTS}

    def _population(self) -> dict[str, Any]:
        parties = [p for p in self.parties.values() if p.cohort != "ER_TEST"]
        total = len(parties)
        tags = Counter(p.cohort for p in parties)
        spec = {"RETAIL_NORMAL": round(1 - sum(COHORT_SHARES.values()), 2), **COHORT_SHARES}
        return {
            "parties_excluding_er_test": total,
            "cohorts": {
                cohort: {"count": tags[cohort], "share": round(tags[cohort] / total, 4) if total else 0.0,
                         "spec_share": spec[cohort]}
                for cohort in COHORT_ORDER
            },
            "real_businesses": sum(1 for p in parties if p.is_business),
            "individual_sole_traders_in_business_cohort": sum(
                1 for p in parties if p.cohort == "BUSINESS_NORMAL" and not p.is_business
            ),
            # Where each scenario's parties came from: its own cohort first,
            # then spilled to ordinary parties when that cohort ran out.
            "scenario_placement": {
                "positives_and_participants": dict(sorted(self._placement["INJECTED_CANDIDATE"].items())),
                "edge_and_boundary": dict(sorted(self._placement["EDGE_AMBIGUOUS"].items())),
            },
        }

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
        self._write_csv(out_dir, LABEL_TRANSACTIONS, self.label_transactions)

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
        if self.target_transactions is not None:
            manifest["target_transactions"] = self.target_transactions
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
            "defect_counts": self._defect_counts(),
            "population": self._population(),
            "label_counts": dict(sorted(self.counters.labels.items())),
            "file_checksums": {f["name"]: f["sha256"] for f in files},
        }
        if self.target_transactions is not None:
            report["scale"] = self._scale_report()
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
            f"profile `{self.profile}`{self._scale_label()}, seed `{self.master_seed}`.",
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
        for spec in (*FILE_SPECS, LABELS, LABEL_TRANSACTIONS):
            lines += [f"## `{spec.name}`", "", spec.purpose, "",
                      "| Field | Type | Mandatory | Allowed values | Rule / defect |", "|---|---|---|---|---|"]
            for f in spec.fields:
                allowed = ", ".join(f"`{v}`" for v in f.allowed) if f.allowed else "—"
                rule = " ".join(part for part in (f.rule, f.defect and f"**Defect:** {f.defect}") if part) or "—"
                lines.append(f"| `{f.name}` | {f.type} | {'yes' if f.required else 'no'} | {allowed} | {rule} |")
            lines.append("")
        produced = self._defect_counts()
        lines += ["## Deliberate quality defects (TRD §11.4)", "",
                  "Produced counts are taken from the rows actually written. An exact-duplicate row counts",
                  "once, as `exact_duplicate`, whatever its source row carried — so a symptom such as an",
                  "unparseable date can appear on a few more rows than its own count.", "",
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
            actual = produced[name]
            lines.append(f"| `{name}` | {rate:.2%} | {handling[name]} (produced: {actual:,}) |")
        return "\n".join(lines) + "\n"

    def _scenario_catalogue(self) -> str:
        lines = [
            "# Scenario catalogue — injected positives, edge cases and control cohort",
            "",
            f"Profile `{self.profile}`{self._scale_label()}, seed `{self.master_seed}`. Every row below has matching rows in",
            "`labels.csv`, so recall and control-cohort tests are mechanical rather than eyeballed (TRD §11.3).",
            "",
            "| Pattern | Scenario | Construction | Expected reason code | Labelled |",
            "|---|---|---|---|---|",
        ]
        constructions = {
            "P01": "One transfer of IDR 2-8bn against an ordinary baseline",
            "P02": "3-5 deposits inside [350M, 500M) within 7 days, aggregate >= 500M",
            "P03": "A credit, then 94-99% of it out again within 1-5 hours",
            "P04": "40-90 payments over 3 days against a much lower baseline",
            "P05": "90+ days with no activity on the account, then a credit of IDR 400M-1.5bn",
            "P06": "6-12 repetitions of one identical round amount within two weeks",
            "P07": "Weekly value stepping up 2-3x for five consecutive weeks",
            "P08": "8-18 remittances concentrated on one listed geography",
            "P09": "One device used by 4-7 otherwise unrelated entities",
            "P10": "20-45 payments at 100-1000x the ticket size the MCC implies",
            "P11": "8-16 senders converging on one account within 5 days, sharing a device",
            "P12": "Customer name one character away from a synthetic list record",
            "ATO": "90-150 quiet days, then 5-15 outgoing payments at 2-8x the customer's usual p95 "
                   "within 6 hours, from a device the customer never used",
        }
        for pattern, name in PATTERN_NAMES.items():
            positives = self.counters.labels.get(f"{pattern}_INJECTED_POSITIVE", 0)
            edges = self.counters.labels.get(f"{pattern}_EDGE_CASE", 0)
            lines.append(
                f"| `{pattern}` | {name} | {constructions[pattern]} | `{EXPECTED_REASON[pattern]}` | "
                f"{positives} positive, {edges} edge |"
            )
        look_alikes = {
            "P01": ("Annual bonus or vehicle down payment: one IDR 60-95M transfer",
                    "exactly IDR 100,000,000 from a customer with 8 transactions in 90 days"),
            "P02": ("Arisan collector: in-band cash deposits always spread past the 7-day window",
                    "3 deposits of exactly IDR 500,000,000 (the threshold is exclusive)"),
            "P03": ("Salary of IDR 20-80M in, 85-95% out to bills and savings the same day, two months",
                    "79% of a IDR 60M credit out within hours"),
            "P04": ("Payroll disburser: 15-40 staff paid within 3 hours on the company's payday, two months",
                    "exactly 14 transactions in 24 hours"),
            "P05": ("Back from working abroad: 90+ quiet days, then IDR 10-40M and ordinary spending",
                    "89 quiet days, then a IDR 30M credit"),
            "P06": ("Agent kiosk: 8 round float top-ups of about IDR 20M in 16 days", "exactly 4 round amounts in 30 days"),
            "P07": ("Seasonal trader: three weeks of 20-35 sales of IDR 2-6M from many buyers", "-"),
            "P08": ("Student abroad: fortnightly family support from a normal corridor", "-"),
            "P09": ("A family of 3-4 at one address sharing one handset", "one device used by exactly 3 entities"),
            "P10": ("B2B supplier: 20-40 payments from 3-5 clients at 1.5-2.5x its category's ticket", "-"),
            "P11": ("School fees: 30-60 payers, one fee, the same names each month, spread over 12 days",
                    "exactly 7 senders within 48 hours, over IDR 200M"),
            "P12": ("A common given name shared with a list record, a different family name", "-"),
        }
        lines += [
            "",
            "## Look-alikes and boundary cases (1.4.0)",
            "",
            "Look-alikes follow the TRD §11.2 examples and the FRD §8 lists of expected false positives, so",
            "some of them do fire the default rule (payroll, float top-ups, a family handset, a B2B supplier,",
            "about half the salaries and returns from abroad); that is the false-positive burden a model",
            "should lower. A boundary case sits exactly on its pattern's own threshold, on the side that",
            "must not fire; the party's own background that could tip it over is carved away. P07, P08, P10",
            "and P12 have none: P07 needs a high-value baseline no ordinary customer has, P08 and P10 fire",
            "on any of several OR-ed sub-conditions, and P12 is the screening engine's.",
            "",
            "| Pattern | Look-alike | Boundary case |",
            "|---|---|---|",
            *(f"| `{p}` | {a} | {b} |" for p, (a, b) in look_alikes.items()),
            "",
            "## ATO look-alikes",
            "",
            "Each carries one or two of the three takeover signals (dormancy, a never-used device, a burst),",
            "never all three, so a detector that keys on any single signal pays for it in false positives.",
            "",
            "| Kind | Signals | Construction |",
            "|---|---|---|",
            "| `RETURNING_NEW_PHONE` | dormancy + new device | 90-150 quiet days, then 2-4 ordinary payments over a few days |",
            "| `NEW_PHONE_PAYDAY` | new device + burst | 5-10 routine payments at the customer's own amounts on payday |",
            "| `RETURNING_SAME_PHONE` | dormancy + burst | 90-150 quiet days, then 3-6 ordinary payments on the usual phone |",
            "",
            "## Ordinary device changes",
            "",
            f"{DEVICE_CHANGE_SHARE:.0%} of individuals start using a new device part-way through the period, so a",
            "device never seen for a customer is common in legitimate traffic too.",
            "",
            "## Counterparties (TRD §11.2)",
            "",
            f"Retail customers send P2P money to {RETAIL_REGULARS[0]}-{RETAIL_REGULARS[1]} regular counterparties, pay "
            f"{FAVOURITE_MERCHANTS[0]}-{FAVOURITE_MERCHANTS[1]} usual merchants and",
            f"{BILLS_PER_CUSTOMER[0]}-{BILLS_PER_CUSTOMER[1]} billers, and top up from {TOPUP_SOURCES[0]}-{TOPUP_SOURCES[1]} "
            f"bank accounts of their own; {NEW_COUNTERPARTY_RATE:.0%} of their",
            f"transfers and merchant payments go to someone new, and {NEW_BECOMES_REGULAR:.0%} of those become regulars. "
            "Businesses have a broad",
            f"payer base: an established one at the start (a third of their payment count), and {NEW_PAYER_RATE:.0%} of",
            "incoming payments from first-time payers. ATO pays only recipients its victim never paid.",
            "",
            "## Background behaviour (TRD §11.2, 1.4.0)",
            "",
            f"Activity is heavy-tailed within the TRD §11.1 total: each entity's share is lognormal (sigma "
            f"{ACTIVITY_SIGMA}), merchants",
            f"{BUSINESS_ACTIVITY:.0f}x as active as retail customers. Retail rows are "
            + ", ".join(f"{w}% {t.lower().replace('_', ' ')}" for _, t, w in RETAIL_MIX) + ";",
            f"top-ups come in (round to IDR {TOPUP_UNIT:,}), bills go out, {REFUND_SHARE:.0%} of merchant payments are "
            f"refunds and {P2P_IN_SHARE:.0%} of",
            "transfers are received. A merchant is paid while it is open (declared hours), at a ticket around its",
            "MCC's typical ticket, and sweeps its takings to its own bank account every week; a sole trader is",
            "paid by transfer.",
            "",
            "## Per-transaction ground truth",
            "",
            "`label_transactions.csv` lists every row each scenario and look-alike emitted, for evaluating a",
            "per-transaction model against exact rows. Like `labels.csv` it is evaluation-only: never loaded",
            "into the app, never a feature input.",
        ]
        population = self._population()
        lines += [
            "",
            "## Population (TRD §11.2)",
            "",
            "Assigned as exact counts, so the shares hold at every scale. Real businesses are all in the",
            "business cohort; §11.1's volumes leave them short of §11.2's share, and individual sole traders",
            "make up the difference.",
            "",
            "| Cohort | Entities | Share | Spec |",
            "|---|---|---|---|",
        ]
        for cohort, entry in population["cohorts"].items():
            lines.append(f"| `{cohort}` | {entry['count']:,} | {entry['share']:.1%} | ~{entry['spec_share']:.0%} |")
        lines += [
            "",
            f"Business cohort: {population['real_businesses']:,} real businesses + "
            f"{population['individual_sole_traders_in_business_cohort']:,} individual sole traders.",
        ]
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
        self.assign_cohorts()
        self.share_beneficial_owners()
        self.build_accounts()
        self.build_merchants()
        self.build_devices()
        self.build_watchlist()
        self.build_background_traffic()
        self.inject_scenarios()
        if self.target_transactions is not None:
            self.calibrate_to_target()
        # Deterministic, and chronological like a real source extract.
        self.transactions.sort(key=lambda r: (r["business_date"], r["source_transaction_reference"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the synthetic raw source dataset (TRD §6.1, §11).")
    scale = parser.add_mutually_exclusive_group()
    scale.add_argument("--profile", choices=sorted(PROFILES),
                       help="tiny=1%% (CI), small=5%% (local dev, default), full=100%% (integration and demo)")
    scale.add_argument("--target-transactions", type=int,
                       help=f"custom scale: derive every volume from this transaction count "
                            f"(min {MIN_TARGET_TRANSACTIONS:,}); lands on it exactly")
    parser.add_argument("--min-per-pattern", type=int, default=None,
                        help=f"custom scale only: floor for each pattern's positives, look-alikes and "
                             f"boundary cases (default {DEFAULT_MIN_PER_PATTERN})")
    parser.add_argument("--seed", type=int, default=20260923, help="master seed; same seed reproduces the dataset")
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date(2026, 9, 30),
                        help="last business date of the six-month period (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output directory (default: sample_data/raw/<profile> or sample_data/raw/custom-<target>)")
    args = parser.parse_args()
    if args.min_per_pattern is not None and args.target_transactions is None:
        parser.error("--min-per-pattern only applies with --target-transactions")

    started = time.perf_counter()
    try:
        if args.target_transactions is not None:
            generator = RawDatasetGenerator(
                seed=args.seed, reference_date=args.reference_date, target_transactions=args.target_transactions,
                min_per_pattern=args.min_per_pattern or DEFAULT_MIN_PER_PATTERN,
            )
            out_dir = args.out or (DEFAULT_OUT / f"{CUSTOM_PROFILE}-{args.target_transactions}")
        else:
            profile = args.profile or "small"
            generator = RawDatasetGenerator(profile=profile, seed=args.seed, reference_date=args.reference_date)
            out_dir = args.out or (DEFAULT_OUT / profile)
    except ValueError as exc:
        parser.error(str(exc))
    generator.generate()
    report = generator.write(out_dir)
    elapsed = time.perf_counter() - started

    print(f"profile={generator.profile}{generator._scale_label()} seed={args.seed} -> {out_dir}  ({elapsed:.1f}s)")
    for name, count in report["row_counts"].items():
        print(f"  {name:<26} {count:>9,}")
    print(f"  {'defects injected':<26} {sum(report['defect_counts'].values()):>9,}")
    print(f"  {'labels written':<26} {sum(report['label_counts'].values()):>9,}")
    if "scale" in report:
        scale = report["scale"]
        calibration = scale["calibration"]
        print(f"  calibration: generated {calibration['generated_before_calibration']:,}, "
              f"+{calibration['added']:,} / -{calibration['removed']:,} -> {calibration['final']:,}")
        floored = [f"{a['pattern']} {a['kind']} {a['proportional']}->{a['applied']}" for a in scale["floor_adjustments"]]
        print(f"  floor (min {scale['min_per_pattern']}): {', '.join(floored) if floored else 'no pattern needed it'}")
        short = [f"{p} {k} {v['placed']}/{v['requested']}" for p, kinds in scale["scenarios"].items()
                 for k, v in kinds.items() if v["placed"] < v["requested"]]
        if short:
            print(f"  ! not all requested scenarios could be placed on distinct entities: {', '.join(short)}")


if __name__ == "__main__":
    main()
