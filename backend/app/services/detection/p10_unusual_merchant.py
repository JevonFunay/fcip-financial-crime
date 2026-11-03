"""P10 Unusual merchant activity (FRD §8.10): a merchant behaving unlike its
declared profile or its own established pattern.

Merchant scope. Per merchant, in a rolling window with at least MIN_TXN_COUNT
transactions, four named sub-conditions are evaluated and the alert says which
fired:
  (a) average ticket at least TICKET_DEVIATION_MULTIPLE times the midpoint of the
      merchant's declared ticket band;
  (b) share of reversed transactions (source status REVERSED) above REFUND_RATE;
  (c) the top TOP_N_PAYERS payers hold more than PAYER_CONCENTRATION of the value
      (only judged when there are more than TOP_N_PAYERS distinct payers, since
      with fewer the share is trivially 100%);
  (d) more than OFF_HOURS_SHARE of the transactions fall outside the declared
      operating hours (Asia/Jakarta).
A sub-condition whose input is missing (no declared band, no hours) is not judged
and is listed as such. The alert is attached to the business that owns the merchant.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.rule import RuleVersion
from app.services.detection import common as c

PATTERN_CODE = "P10_UNUSUAL_MERCHANT"
TEMPLATE_VERSION = "p10_unusual_merchant_v1"
REFUND_STATUS = "REVERSED"


@dataclass(frozen=True)
class P10Parameters:
    ticket_deviation_multiple: Decimal
    ticket_band_midpoints: dict[str, Decimal]
    refund_rate: Decimal
    payer_concentration: Decimal
    top_n_payers: int
    off_hours_share: Decimal
    min_txn_count: int
    window_days: int  # from the rule version's window length
    currency: str

    @classmethod
    def from_rule_version(cls, version: RuleVersion) -> "P10Parameters":
        window = c.require_window(version, TEMPLATE_VERSION, rolling=True, unit=timedelta(days=1))
        v, t = version.parameters, TEMPLATE_VERSION
        raw = c.require(v, "ticket_band_midpoints", t)
        if not isinstance(raw, dict) or not raw:
            raise ValueError(f"{t}: parameter 'ticket_band_midpoints' must map each declared band to a midpoint")
        return cls(
            ticket_deviation_multiple=c.as_decimal(v, "ticket_deviation_multiple", t, minimum=Decimal(1)),
            ticket_band_midpoints={k: Decimal(str(x)) for k, x in raw.items()},
            refund_rate=c.as_ratio(v, "refund_rate", t),
            payer_concentration=c.as_ratio(v, "payer_concentration", t),
            top_n_payers=c.as_int(v, "top_n_payers", t, minimum=1),
            off_hours_share=c.as_ratio(v, "off_hours_share", t),
            min_txn_count=c.as_int(v, "min_txn_count", t, minimum=1),
            window_days=window.days,
            currency=c.as_text(v, "currency", t),
        )


def _within_hours(moment, declared: str) -> bool | None:
    """Is the local time of `moment` inside "HH:MM-HH:MM"? None when the declaration is unusable."""
    try:
        opens_text, closes_text = declared.split("-")
        opens = time.fromisoformat(opens_text.strip())
        closes = time.fromisoformat(closes_text.strip())
    except ValueError:
        return None
    local = moment.astimezone(c.LOCAL_TZ).time()
    if opens <= closes:
        return opens <= local <= closes
    return local >= opens or local <= closes  # opens before midnight, closes after


def evaluate(db: Session, params: P10Parameters, *, period=None, cache: c.RowCache | None = None) -> c.PatternEvaluation:
    rows = (cache or c.RowCache(db)).rows(params.currency)
    merchants = {
        m.merchant_ref: (m, ref)
        for m, ref in db.execute(select(Merchant, Customer.customer_ref).join(Customer, Merchant.customer_id == Customer.id))
    }
    span = timedelta(days=params.window_days)
    candidates: list[c.PatternCandidate] = []
    unknown = 0
    groups = c.group_by((r for r in rows if r.merchant_ref), lambda r: r.merchant_ref)
    for merchant_ref, merchant_rows in groups.items():
        if merchant_ref not in merchants:
            unknown += 1
            continue
        merchant, owner_ref = merchants[merchant_ref]
        merchant_rows.sort(key=lambda r: (r.date, r.ref))
        i = 0
        while i < len(merchant_rows):
            start = merchant_rows[i].date
            end = start + span
            j = i
            while j < len(merchant_rows) and merchant_rows[j].date < end:
                j += 1
            window = tuple(merchant_rows[i:j])
            if len(window) < params.min_txn_count:
                i += 1
                continue
            total = sum((t.amount for t in window), Decimal(0))
            met: list[str] = []
            not_judged: list[str] = []
            avg_ticket = total / len(window)
            midpoint = params.ticket_band_midpoints.get(merchant.declared_ticket_band or "")
            ticket_multiple = None
            if midpoint is None:
                not_judged.append("TICKET")
            else:
                ticket_multiple = avg_ticket / midpoint
                if ticket_multiple >= params.ticket_deviation_multiple:
                    met.append("TICKET")
            refunds = sum(1 for t in window if t.status == REFUND_STATUS)
            refund_rate = Decimal(refunds) / len(window)
            if refund_rate > params.refund_rate:
                met.append("REFUND")
            by_payer: dict = defaultdict(Decimal)
            for t in window:
                by_payer[t.customer_id] += t.amount
            concentration = None
            if len(by_payer) > params.top_n_payers:
                top = sorted(by_payer.values(), reverse=True)[: params.top_n_payers]
                concentration = sum(top, Decimal(0)) / total if total else Decimal(0)
                if concentration > params.payer_concentration:
                    met.append("CONCENTRATION")
            else:
                not_judged.append("CONCENTRATION")
            off_share = None
            if merchant.operating_hours:
                flags = [_within_hours(t.date, merchant.operating_hours) for t in window]
                if None in flags:
                    not_judged.append("OFF_HOURS")
                else:
                    off_share = Decimal(sum(1 for f in flags if not f)) / len(window)
                    if off_share > params.off_hours_share:
                        met.append("OFF_HOURS")
            else:
                not_judged.append("OFF_HOURS")
            if met and c.in_period(start, period):
                top_payers = sorted(by_payer, key=lambda k: (-by_payer[k], str(k)))[: params.top_n_payers]
                refs = {t.customer_id: t.customer_ref for t in window}
                candidates.append(
                    c.PatternCandidate(
                        customer_id=merchant.customer_id, customer_ref=owner_ref, transactions=window,
                        window_start=start, window_end=end, anchor=start,
                        factors={
                            "SUB_CONDITION_MET": met,
                            "SUB_CONDITION_NOT_JUDGED": not_judged,
                            "MERCHANT_ID": merchant_ref,
                            "MCC": merchant.mcc,
                            "AVG_TICKET": c.money(avg_ticket),
                            "CATEGORY_BAND": merchant.declared_ticket_band,
                            "TICKET_MULTIPLE": str(ticket_multiple.quantize(c.CENTS)) if ticket_multiple is not None else None,
                            "REFUND_RATE": str(refund_rate.quantize(Decimal("0.0001"))),
                            "REFUND_COUNT": refunds,
                            "PAYER_CONCENTRATION": str(concentration.quantize(Decimal("0.0001"))) if concentration is not None else None,
                            "TOP5_PAYER_IDS": [refs[k] for k in top_payers],
                            "OFF_HOURS_SHARE": str(off_share.quantize(Decimal("0.0001"))) if off_share is not None else None,
                            "TXN_COUNT_WINDOW": len(window),
                            "MERCHANT_AGE_DAYS": "DATA_UNAVAILABLE",
                            "TICKET_DEVIATION_MULTIPLE_APPLIED": str(params.ticket_deviation_multiple),
                            "REFUND_RATE_APPLIED": str(params.refund_rate),
                            "PAYER_CONCENTRATION_APPLIED": str(params.payer_concentration),
                            "OFF_HOURS_SHARE_APPLIED": str(params.off_hours_share),
                        },
                        reason=(
                            f"P10 Unusual merchant: {merchant_ref} ({merchant.mcc}) met {', '.join(x.lower() for x in met)} "
                            f"over {len(window)} transactions from {start.astimezone(timezone.utc).date()}"
                        ),
                    )
                )
                i = j
            else:
                i += 1
    notes = {"merchant reference(s) with no merchant master record": unknown} if unknown else {}
    return c.PatternEvaluation(len(rows), len(groups), candidates, notes)


TEMPLATE = c.PatternTemplate(PATTERN_CODE, TEMPLATE_VERSION, P10Parameters.from_rule_version, evaluate,
                             "A merchant whose ticket size, reversals, payer mix or hours depart from its profile")
