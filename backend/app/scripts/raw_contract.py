"""The source file contract from TRD §6.1, as data.

The generator writes CSVs from these specs and the data dictionary is rendered
from the same specs, so the dictionary cannot drift from what is produced.
`*` in TRD §6.1 means mandatory; that is `required=True` here.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool = False
    allowed: tuple[str, ...] = ()
    rule: str = ""
    defect: str = ""


@dataclass(frozen=True)
class FileSpec:
    name: str
    entity_kind: str
    purpose: str
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)

    @property
    def header(self) -> list[str]:
        return [f.name for f in self.fields]


ISO_COUNTRY = "ISO-3166-1 alpha-2"
KYC_STATUS = ("COMPLETE", "PARTIAL", "PENDING")
ACCOUNT_STATUS = ("ACTIVE", "DORMANT", "CLOSED", "RESTRICTED")
PARTY_STATUS = ("ACTIVE", "SUSPENDED", "CLOSED")
DIRECTION = ("IN", "OUT")
LIST_TYPE = ("PEP_SYNTHETIC", "SANCTIONS_SYNTHETIC", "INTERNAL_WATCH")
CONTROL_TYPE = ("OWNERSHIP", "VOTING_RIGHTS", "BOARD_CONTROL", "OTHER_SIGNIFICANT_INFLUENCE")
SOURCE_STATUS = ("SETTLED", "PENDING", "REVERSED")
OWNER_TYPE = ("INDIVIDUAL", "BUSINESS")

CUSTOMERS = FileSpec(
    "customers.csv",
    "CUSTOMER",
    "Individual wallet holders (FRD E01).",
    (
        FieldSpec("source_customer_id", "string", True, rule="Unique within the source system"),
        FieldSpec("full_name", "string", True, rule="Generated pool; 3% truncated or single-token", defect="3% single-token / truncated names"),
        FieldSpec("aliases", "string", rule="Semicolon-separated; blank when none"),
        FieldSpec("date_of_birth", "date", True, rule="YYYY-MM-DD; <= today - 17 years"),
        FieldSpec("place_of_birth", "string"),
        FieldSpec("gender", "enum", allowed=("M", "F")),
        FieldSpec("nationality", "string", True, rule=ISO_COUNTRY),
        FieldSpec(
            "national_id_reference",
            "string",
            True,
            rule="16 digits, NIK-shaped, province prefix 99 (never issued) so it cannot collide with a real NIK; hashed on load",
        ),
        FieldSpec("occupation", "string"),
        FieldSpec("income_band", "enum", allowed=("LT_5M", "5M_15M", "15M_50M", "50M_200M", "GT_200M")),
        FieldSpec("address_line", "string", rule="2% placeholder addresses", defect="2% placeholder -> LOW_SPECIFICITY edge"),
        FieldSpec("city", "string"),
        FieldSpec("province", "string"),
        FieldSpec("postcode", "string", rule="5 digits"),
        FieldSpec("country", "string", True, rule=ISO_COUNTRY),
        FieldSpec("phone", "string", rule="E.164; synthetic +6289 block"),
        FieldSpec("email", "string", rule="Synthetic domains only"),
        FieldSpec("onboarding_date", "date", True),
        FieldSpec("kyc_status", "enum", True, allowed=KYC_STATUS),
        FieldSpec("customer_status", "enum", True, allowed=PARTY_STATUS),
    ),
)

BUSINESS_CUSTOMERS = FileSpec(
    "business_customers.csv",
    "BUSINESS_CUSTOMER",
    "Legal entities holding accounts or operating as merchants (FRD E02).",
    (
        FieldSpec("source_business_id", "string", True),
        FieldSpec("legal_name", "string", True),
        FieldSpec("trading_names", "string", rule="Semicolon-separated"),
        FieldSpec("registration_number", "string", True, rule="Synthetic NIB-shaped, 13 digits prefixed 99"),
        FieldSpec("registration_country", "string", True, rule=ISO_COUNTRY),
        FieldSpec("incorporation_date", "date", True),
        FieldSpec("industry_code", "string", True, rule="Synthetic KBLI-shaped 5-digit code"),
        FieldSpec("declared_turnover_band", "enum", allowed=("LT_300M", "300M_2_5B", "2_5B_50B", "GT_50B")),
        FieldSpec("address_line", "string"),
        FieldSpec("city", "string"),
        FieldSpec("province", "string"),
        FieldSpec("postcode", "string"),
        FieldSpec("country", "string", True, rule=ISO_COUNTRY),
        FieldSpec("contact_phone", "string"),
        FieldSpec("contact_email", "string"),
        FieldSpec("status", "enum", True, allowed=PARTY_STATUS),
    ),
)

BENEFICIAL_OWNERS = FileSpec(
    "beneficial_owners.csv",
    "BENEFICIAL_OWNER",
    "Natural persons behind a business customer (FRD E03).",
    (
        FieldSpec("source_bo_id", "string", True),
        FieldSpec("source_business_id", "string", True, rule="Must resolve within the batch or already exist"),
        FieldSpec("full_name", "string", True),
        FieldSpec("date_of_birth", "date"),
        FieldSpec("nationality", "string", rule=ISO_COUNTRY),
        FieldSpec("ownership_percentage", "decimal", True, rule="(0, 100]"),
        FieldSpec("control_type", "enum", True, allowed=CONTROL_TYPE),
        FieldSpec("declared_date", "date"),
    ),
)

ACCOUNTS = FileSpec(
    "accounts.csv",
    "ACCOUNT",
    "Funding instruments through which transactions occur (FRD E04).",
    (
        FieldSpec("source_account_id", "string", True),
        FieldSpec("owner_source_id", "string", True, rule="A source_customer_id or source_business_id"),
        FieldSpec("owner_type", "enum", True, allowed=OWNER_TYPE),
        FieldSpec("account_type", "enum", True, allowed=("WALLET", "VIRTUAL_ACCOUNT", "SETTLEMENT")),
        FieldSpec("currency", "string", True, rule="Must exist in the reference currency list"),
        FieldSpec("opened_date", "date", True),
        FieldSpec("closed_date", "date", rule=">= opened_date; blank when open"),
        FieldSpec(
            "status",
            "enum",
            True,
            allowed=ACCOUNT_STATUS,
            rule="Read-only reference data from source; the platform never writes it (NG-01). "
            "Dormancy for P05 is derived from transaction history, not from this field.",
        ),
        FieldSpec("balance_snapshot", "decimal", rule="IDR, 2dp"),
        FieldSpec("balance_snapshot_at", "timestamp"),
    ),
)

MERCHANTS = FileSpec(
    "merchants.csv",
    "MERCHANT",
    "Acceptance points taking payments (FRD E05).",
    (
        FieldSpec("source_merchant_id", "string", True),
        FieldSpec("source_business_id", "string", True),
        FieldSpec("merchant_name", "string", True),
        FieldSpec("mcc", "string", True, rule="4-digit MCC drawn from ~25 bands; drives the P10 expectation band"),
        FieldSpec("declared_expected_volume_band", "enum", allowed=("LT_50M", "50M_250M", "250M_1B", "GT_1B")),
        FieldSpec("declared_expected_ticket_band", "enum", allowed=("LT_50K", "50K_250K", "250K_1M", "GT_1M")),
        FieldSpec("onboarded_date", "date", True),
        FieldSpec("status", "enum", True, allowed=PARTY_STATUS),
        FieldSpec("settlement_source_account_id", "string", rule="A SETTLEMENT account of the same business"),
        FieldSpec("operating_hours_declared", "string", rule="HH:MM-HH:MM local"),
        FieldSpec("outlet_count", "integer"),
    ),
)

DEVICES = FileSpec(
    "devices.csv",
    "DEVICE",
    "Hardware/app instances used to transact (FRD E06).",
    (
        FieldSpec("source_device_id", "string", True),
        FieldSpec("device_type", "enum", True, allowed=("ANDROID_PHONE", "IOS_PHONE", "TABLET", "WEB_BROWSER")),
        FieldSpec("os_family", "string"),
        FieldSpec("app_version", "string"),
        FieldSpec("is_emulator", "boolean"),
        FieldSpec("is_rooted", "boolean"),
        FieldSpec("first_seen", "timestamp", True),
        FieldSpec("last_seen", "timestamp"),
    ),
)

TRANSACTIONS = FileSpec(
    "transactions.csv",
    "TRANSACTION",
    "Atomic movements of value; the core unit of observation (FRD E11).",
    (
        FieldSpec("source_transaction_reference", "string", True, rule="Unique per (source_system, business_date)"),
        FieldSpec("source_account_id", "string", True, rule="0.5% deliberately unresolvable", defect="0.5% UNRESOLVED_ACCOUNT"),
        FieldSpec("direction", "enum", True, allowed=DIRECTION),
        FieldSpec("amount_original", "decimal", True, rule="> 0, 2dp", defect="0.2% zero/negative -> INVALID_AMOUNT"),
        FieldSpec("currency_original", "string", True, rule=ISO_COUNTRY + "-style ISO-4217 code", defect="0.2% invalid -> INVALID_CURRENCY"),
        FieldSpec("value_datetime", "timestamp", True, rule="ISO 8601 with explicit +07:00 offset", defect="0.4% malformed -> INVALID_DATE_FORMAT"),
        FieldSpec("business_date", "date", True, rule="Asia/Jakarta calendar day of value_datetime"),
        FieldSpec("channel", "enum", True, allowed=("CASH", "TRANSFER", "QRIS", "TOPUP", "PAYMENT", "REMITTANCE", "AGENT")),
        FieldSpec(
            "transaction_type",
            "enum",
            True,
            allowed=("P2P_TRANSFER", "MERCHANT_PAYMENT", "CASH_DEPOSIT", "CASH_WITHDRAWAL", "WALLET_TOPUP", "BILL_PAYMENT", "INBOUND_REMITTANCE", "SETTLEMENT"),
        ),
        FieldSpec("counterparty_reference", "string", rule="3% deliberately missing", defect="3% missing -> referential-integrity metric"),
        FieldSpec("counterparty_name", "string"),
        FieldSpec("counterparty_country", "string", rule=ISO_COUNTRY),
        FieldSpec("source_merchant_id", "string", rule="Set for merchant payments only"),
        FieldSpec("source_device_id", "string", rule="5% deliberately missing", defect="5% missing -> feature DATA_UNAVAILABLE"),
        FieldSpec("ip_address", "string", rule="Synthetic ranges only (198.51.100.0/24, 203.0.113.0/24)"),
        FieldSpec("status_from_source", "enum", True, allowed=SOURCE_STATUS, rule="Reference data; never written by the platform (FR-309)"),
    ),
)

WATCHLIST = FileSpec(
    "watchlist.csv",
    "WATCHLIST_RECORD",
    "Synthetic list entries screening refers to (FRD E13). Never loadable without synthetic_declaration=true (FR-501).",
    (
        FieldSpec("list_record_id", "string", True),
        FieldSpec("list_type", "enum", True, allowed=LIST_TYPE, rule="60% PEP, 30% sanctions, 10% internal (TRD §11.1)"),
        FieldSpec("primary_name", "string", True),
        FieldSpec("aliases", "string"),
        FieldSpec("date_of_birth", "date"),
        FieldSpec("nationality", "string", rule=ISO_COUNTRY),
        FieldSpec("country", "string", rule=ISO_COUNTRY),
        FieldSpec("position_or_role", "string"),
        FieldSpec("note", "string"),
    ),
)

LABELS = FileSpec(
    "labels.csv",
    "LABEL",
    "Ground truth for every injected scenario (TRD §11.3), so recall and control-cohort tests are mechanical.",
    (
        FieldSpec("scenario_id", "string", True),
        FieldSpec("label_type", "enum", True, allowed=("INJECTED_POSITIVE", "EDGE_CASE", "CONTROL_CLEAN")),
        FieldSpec("pattern_code", "string", True),
        FieldSpec("entity_source_id", "string", True),
        FieldSpec("window_start", "date"),
        FieldSpec("window_end", "date"),
        FieldSpec("expected_reason_code", "string"),
        FieldSpec("note", "string"),
    ),
)

# Written in the order the ingestion pipeline must load them (TRD §6.2:
# party -> account -> merchant -> device -> transaction).
FILE_SPECS: tuple[FileSpec, ...] = (
    CUSTOMERS,
    BUSINESS_CUSTOMERS,
    BENEFICIAL_OWNERS,
    ACCOUNTS,
    MERCHANTS,
    DEVICES,
    TRANSACTIONS,
    WATCHLIST,
)

CONTRACT_VERSION = "1"
GENERATOR_VERSION = "1.0.0"
