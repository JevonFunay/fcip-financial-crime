"""P08 High-risk geography exposure (FRD §8.8): transactions with counterparties
in countries on the organisation's own, synthetic, versioned list.

The list is part of the rule version's parameters (COUNTRIES with a weight and
LIST_VERSION), so an alert always names the list it was judged against and a
later change to the list never alters it (BR-1107.2). It is a fictional
construct for a fictional company, not an official designation of any country.

Only the counterparty country is available today; the IP country of a session
is not part of the data, so the IP-versus-registered-country factor is reported
as unavailable rather than invented. The rule fires within a rolling window when
the exposed count, the exposed value, or the exposed share of the subject's own
value reaches its minimum.
"""

from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P08_HIGH_RISK_GEO"
TEMPLATE_VERSION = "p08_high_risk_geo_v1"


@dataclass(frozen=True)
class P08Parameters:
    countries: dict[str, Decimal]  # ISO-3166 alpha-2 -> weight
    list_version: str
    min_count: int
    min_aggregate: Decimal
    min_exposure_share: Decimal
    window_days: int  # from the rule version's window length
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P08Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(days=1))
        v, t = version.parameters, TEMPLATE_VERSION
        raw = c.require(v, "countries", t)
        if not isinstance(raw, dict) or not raw:
            raise ValueError(f"{t}: parameter 'countries' must list at least one country with a weight")
        countries = {}
        for code, weight in raw.items():
            if not (isinstance(code, str) and len(code) == 2 and code.isupper()):
                raise ValueError(f"{t}: '{code}' is not an ISO-3166 alpha-2 code")
            countries[code] = Decimal(str(weight))
        return cls(
            countries=countries,
            list_version=c.as_text(v, "list_version", t),
            min_count=c.as_int(v, "min_count", t, minimum=1),
            min_aggregate=c.as_decimal(v, "min_aggregate", t, minimum=Decimal(1)),
            min_exposure_share=c.as_ratio(v, "min_exposure_share", t),
            window_days=window.days,
            currency=c.as_text(v, "currency", t),
        )


def evaluate(db: Session, params: P08Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    span = timedelta(days=params.window_days)
    candidates: list[c.PatternCandidate] = []
    customers = c.group_by(rows, lambda r: r.customer_id)
    for customer_rows in customers.values():
        i = 0
        while i < len(customer_rows):
            if customer_rows[i].counterparty_country not in params.countries:
                i += 1
                continue
            start = customer_rows[i].date
            end = start + span
            j = i
            while j < len(customer_rows) and customer_rows[j].date < end:
                j += 1
            window = customer_rows[i:j]
            exposed = tuple(t for t in window if t.counterparty_country in params.countries)
            exposed_value = sum((t.amount for t in exposed), Decimal(0))
            total_value = sum((t.amount for t in window), Decimal(0))
            share = exposed_value / total_value if total_value else Decimal(0)
            met = [
                name for name, hit in (
                    ("COUNT", len(exposed) >= params.min_count),
                    ("AGGREGATE", exposed_value >= params.min_aggregate),
                    ("SHARE", share >= params.min_exposure_share),
                ) if hit
            ]
            if met and c.in_period(start, period):
                codes = sorted({t.counterparty_country for t in exposed})
                candidates.append(
                    c.PatternCandidate(
                        customer_id=exposed[0].customer_id, customer_ref=exposed[0].customer_ref, transactions=exposed,
                        window_start=start, window_end=end, anchor=start,
                        factors={
                            "COUNTRY_CODES": codes,
                            "LIST_VERSION": params.list_version,
                            "COUNTRY_WEIGHTS": {code: str(params.countries[code]) for code in codes},
                            "TXN_COUNT": len(exposed),
                            "AGGREGATE_AMOUNT": c.money(exposed_value),
                            "EXPOSURE_SHARE": str(share.quantize(Decimal("0.0001"))),
                            "EXPOSURE_SOURCE": "COUNTERPARTY_COUNTRY",
                            "IP_VS_REGISTERED_MISMATCH": "DATA_UNAVAILABLE",
                            "MIN_COUNT_APPLIED": params.min_count,
                            "MIN_AGGREGATE_APPLIED": c.money(params.min_aggregate),
                            "MIN_EXPOSURE_SHARE_APPLIED": str(params.min_exposure_share),
                            "CONDITIONS_MET": met,
                        },
                        reason=(
                            f"P08 High-risk geography: {len(exposed)} transaction(s) totalling {params.currency} "
                            f"{exposed_value:,.0f} ({share:.0%} of the subject's value) with {', '.join(codes)} "
                            f"on list {params.list_version}, from {start.astimezone(timezone.utc).date()}"
                        ),
                    )
                )
                i = j
            else:
                i += 1
    return c.PatternEvaluation(len(rows), len(customers), candidates)


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P08Parameters.from_rule_version, evaluate,
                             "Exposure to counterparties in countries on the organisation's own synthetic list")
