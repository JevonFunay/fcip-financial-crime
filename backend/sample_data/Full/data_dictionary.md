# Data dictionary — FCIP synthetic raw source files

Generator `1.0.0`, contract version `1`, profile `full`, seed `20260923`.

Generated from `app/scripts/raw_contract.py`, which is also what the generator writes the
CSVs from — this file cannot describe columns that were not produced.

All data is synthetic (CN-02). National IDs use province prefix `99`, which is never issued;
registration numbers use the same prefix; phones sit in a reserved `+62899` block; IP addresses
come from the RFC 5737 documentation ranges. Nothing here is derived from a real person,
document or list.

Format (TRD §6.1): UTF-8, comma-separated, every field quoted, header row, `\n` line endings.

## `customers.csv`

Individual wallet holders (FRD E01).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_customer_id` | string | yes | — | Unique within the source system |
| `full_name` | string | yes | — | Generated pool; 3% truncated or single-token **Defect:** 3% single-token / truncated names |
| `aliases` | string | no | — | Semicolon-separated; blank when none |
| `date_of_birth` | date | yes | — | YYYY-MM-DD; <= today - 17 years |
| `place_of_birth` | string | no | — | — |
| `gender` | enum | no | `M`, `F` | — |
| `nationality` | string | yes | — | ISO-3166-1 alpha-2 |
| `national_id_reference` | string | yes | — | 16 digits, NIK-shaped, province prefix 99 (never issued) so it cannot collide with a real NIK; hashed on load |
| `occupation` | string | no | — | — |
| `income_band` | enum | no | `LT_5M`, `5M_15M`, `15M_50M`, `50M_200M`, `GT_200M` | — |
| `address_line` | string | no | — | 2% placeholder addresses **Defect:** 2% placeholder -> LOW_SPECIFICITY edge |
| `city` | string | no | — | — |
| `province` | string | no | — | — |
| `postcode` | string | no | — | 5 digits |
| `country` | string | yes | — | ISO-3166-1 alpha-2 |
| `phone` | string | no | — | E.164; synthetic +6289 block |
| `email` | string | no | — | Synthetic domains only |
| `onboarding_date` | date | yes | — | — |
| `kyc_status` | enum | yes | `COMPLETE`, `PARTIAL`, `PENDING` | — |
| `customer_status` | enum | yes | `ACTIVE`, `SUSPENDED`, `CLOSED` | — |

## `business_customers.csv`

Legal entities holding accounts or operating as merchants (FRD E02).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_business_id` | string | yes | — | — |
| `legal_name` | string | yes | — | — |
| `trading_names` | string | no | — | Semicolon-separated |
| `registration_number` | string | yes | — | Synthetic NIB-shaped, 13 digits prefixed 99 |
| `registration_country` | string | yes | — | ISO-3166-1 alpha-2 |
| `incorporation_date` | date | yes | — | — |
| `industry_code` | string | yes | — | Synthetic KBLI-shaped 5-digit code |
| `declared_turnover_band` | enum | no | `LT_300M`, `300M_2_5B`, `2_5B_50B`, `GT_50B` | — |
| `address_line` | string | no | — | — |
| `city` | string | no | — | — |
| `province` | string | no | — | — |
| `postcode` | string | no | — | — |
| `country` | string | yes | — | ISO-3166-1 alpha-2 |
| `contact_phone` | string | no | — | — |
| `contact_email` | string | no | — | — |
| `status` | enum | yes | `ACTIVE`, `SUSPENDED`, `CLOSED` | — |

## `beneficial_owners.csv`

Natural persons behind a business customer (FRD E03).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_bo_id` | string | yes | — | — |
| `source_business_id` | string | yes | — | Must resolve within the batch or already exist |
| `full_name` | string | yes | — | — |
| `date_of_birth` | date | no | — | — |
| `nationality` | string | no | — | ISO-3166-1 alpha-2 |
| `ownership_percentage` | decimal | yes | — | (0, 100] |
| `control_type` | enum | yes | `OWNERSHIP`, `VOTING_RIGHTS`, `BOARD_CONTROL`, `OTHER_SIGNIFICANT_INFLUENCE` | — |
| `declared_date` | date | no | — | — |

## `accounts.csv`

Funding instruments through which transactions occur (FRD E04).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_account_id` | string | yes | — | — |
| `owner_source_id` | string | yes | — | A source_customer_id or source_business_id |
| `owner_type` | enum | yes | `INDIVIDUAL`, `BUSINESS` | — |
| `account_type` | enum | yes | `WALLET`, `VIRTUAL_ACCOUNT`, `SETTLEMENT` | — |
| `currency` | string | yes | — | Must exist in the reference currency list |
| `opened_date` | date | yes | — | — |
| `closed_date` | date | no | — | >= opened_date; blank when open |
| `status` | enum | yes | `ACTIVE`, `DORMANT`, `CLOSED`, `RESTRICTED` | Read-only reference data from source; the platform never writes it (NG-01). Dormancy for P05 is derived from transaction history, not from this field. |
| `balance_snapshot` | decimal | no | — | IDR, 2dp |
| `balance_snapshot_at` | timestamp | no | — | — |

## `merchants.csv`

Acceptance points taking payments (FRD E05).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_merchant_id` | string | yes | — | — |
| `source_business_id` | string | yes | — | — |
| `merchant_name` | string | yes | — | — |
| `mcc` | string | yes | — | 4-digit MCC drawn from ~25 bands; drives the P10 expectation band |
| `declared_expected_volume_band` | enum | no | `LT_50M`, `50M_250M`, `250M_1B`, `GT_1B` | — |
| `declared_expected_ticket_band` | enum | no | `LT_50K`, `50K_250K`, `250K_1M`, `GT_1M` | — |
| `onboarded_date` | date | yes | — | — |
| `status` | enum | yes | `ACTIVE`, `SUSPENDED`, `CLOSED` | — |
| `settlement_source_account_id` | string | no | — | A SETTLEMENT account of the same business |
| `operating_hours_declared` | string | no | — | HH:MM-HH:MM local |
| `outlet_count` | integer | no | — | — |

## `devices.csv`

Hardware/app instances used to transact (FRD E06).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_device_id` | string | yes | — | — |
| `device_type` | enum | yes | `ANDROID_PHONE`, `IOS_PHONE`, `TABLET`, `WEB_BROWSER` | — |
| `os_family` | string | no | — | — |
| `app_version` | string | no | — | — |
| `is_emulator` | boolean | no | — | — |
| `is_rooted` | boolean | no | — | — |
| `first_seen` | timestamp | yes | — | — |
| `last_seen` | timestamp | no | — | — |

## `transactions.csv`

Atomic movements of value; the core unit of observation (FRD E11).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `source_transaction_reference` | string | yes | — | Unique per (source_system, business_date) |
| `source_account_id` | string | yes | — | 0.5% deliberately unresolvable **Defect:** 0.5% UNRESOLVED_ACCOUNT |
| `direction` | enum | yes | `IN`, `OUT` | — |
| `amount_original` | decimal | yes | — | > 0, 2dp **Defect:** 0.2% zero/negative -> INVALID_AMOUNT |
| `currency_original` | string | yes | — | ISO-3166-1 alpha-2-style ISO-4217 code **Defect:** 0.2% invalid -> INVALID_CURRENCY |
| `value_datetime` | timestamp | yes | — | ISO 8601 with explicit +07:00 offset **Defect:** 0.4% malformed -> INVALID_DATE_FORMAT |
| `business_date` | date | yes | — | Asia/Jakarta calendar day of value_datetime |
| `channel` | enum | yes | `CASH`, `TRANSFER`, `QRIS`, `TOPUP`, `PAYMENT`, `REMITTANCE`, `AGENT` | — |
| `transaction_type` | enum | yes | `P2P_TRANSFER`, `MERCHANT_PAYMENT`, `CASH_DEPOSIT`, `CASH_WITHDRAWAL`, `WALLET_TOPUP`, `BILL_PAYMENT`, `INBOUND_REMITTANCE`, `SETTLEMENT` | — |
| `counterparty_reference` | string | no | — | 3% deliberately missing **Defect:** 3% missing -> referential-integrity metric |
| `counterparty_name` | string | no | — | — |
| `counterparty_country` | string | no | — | ISO-3166-1 alpha-2 |
| `source_merchant_id` | string | no | — | Set for merchant payments only |
| `source_device_id` | string | no | — | 5% deliberately missing **Defect:** 5% missing -> feature DATA_UNAVAILABLE |
| `ip_address` | string | no | — | Synthetic ranges only (198.51.100.0/24, 203.0.113.0/24) |
| `status_from_source` | enum | yes | `SETTLED`, `PENDING`, `REVERSED` | Reference data; never written by the platform (FR-309) |

## `watchlist.csv`

Synthetic list entries screening refers to (FRD E13). Never loadable without synthetic_declaration=true (FR-501).

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `list_record_id` | string | yes | — | — |
| `list_type` | enum | yes | `PEP_SYNTHETIC`, `SANCTIONS_SYNTHETIC`, `INTERNAL_WATCH` | 60% PEP, 30% sanctions, 10% internal (TRD §11.1) |
| `primary_name` | string | yes | — | — |
| `aliases` | string | no | — | — |
| `date_of_birth` | date | no | — | — |
| `nationality` | string | no | — | ISO-3166-1 alpha-2 |
| `country` | string | no | — | ISO-3166-1 alpha-2 |
| `position_or_role` | string | no | — | — |
| `note` | string | no | — | — |

## `labels.csv`

Ground truth for every injected scenario (TRD §11.3), so recall and control-cohort tests are mechanical.

| Field | Type | Mandatory | Allowed values | Rule / defect |
|---|---|---|---|---|
| `scenario_id` | string | yes | — | — |
| `label_type` | enum | yes | `INJECTED_POSITIVE`, `EDGE_CASE`, `CONTROL_CLEAN` | — |
| `pattern_code` | string | yes | — | — |
| `entity_source_id` | string | yes | — | — |
| `window_start` | date | no | — | — |
| `window_end` | date | no | — | — |
| `expected_reason_code` | string | no | — | — |
| `note` | string | no | — | — |

## Deliberate quality defects (TRD §11.4)

| Defect | Target rate | Expected handling |
|---|---|---|
| `missing_counterparty` | 3.00% | Loaded; lowers referential-integrity metric; drives the Entity 360 banner (produced: 11,899) |
| `missing_device` | 5.00% | Loaded; feature marked `DATA_UNAVAILABLE` (produced: 19,457) |
| `malformed_date` | 0.40% | Quarantined `INVALID_DATE_FORMAT` (produced: 1,578) |
| `invalid_currency` | 0.20% | Quarantined `INVALID_CURRENCY` (produced: 822) |
| `invalid_amount` | 0.20% | Quarantined `INVALID_AMOUNT` (produced: 828) |
| `unresolved_account` | 0.50% | Held to end of batch, then quarantined `UNRESOLVED_ACCOUNT` (produced: 2,034) |
| `exact_duplicate` | 1.00% | Suppressed by idempotency, still counted (produced: 3,948) |
| `idempotency_conflict` | 0.05% | Quarantined `IDEMPOTENCY_CONFLICT` (produced: 199) |
| `late_arrival` | 1.50% | Flagged `LATE_ARRIVAL`, triggers targeted re-evaluation (produced: 5,993) |
| `missing_bo` | 4.00% | Loaded; flagged `BO_MISSING`; feeds a risk factor (produced: 48) |
| `placeholder_address` | 2.00% | Loaded; produces a `LOW_SPECIFICITY` graph edge (produced: 195) |
| `truncated_name` | 3.00% | Loaded; some become `NOT_SCREENABLE`, others `HIGH_FREQUENCY_NAME` (produced: 267) |
