"""Scenario-artefact audit for a generated dataset (PROJECT_CONTEXT §8).

A scenario should differ from ordinary traffic only in the behaviour it
represents. Any other difference is a trace of how the generator built it,
and a model trained on the data learns that trace instead of the behaviour.
Generator 1.1.0 had such traces: scenario rows almost never carried a device
(5.5% missing in background, up to 98.6% in a scenario), and P03's credits all
arrived at 09:xx.

For every raw transaction column (as a categorical "view": hour, weekday,
channel, missing device, last digit of the amount, ...) this compares the rows
each scenario group emitted with background rows, using total variation
distance (TVD, 0 = identical distributions, 1 = disjoint). A view is flagged
when the distance clearly exceeds sampling noise AND the view is not part of
that pattern's definition. Views that *are* the definition (the amount of a
large transfer, the country in a geography pattern, the shared device in P09)
are reported but never flagged. Some views are conditional and only look at
the rows they apply to: device attributes only where a device is present,
merchant presence only on merchant payments. Otherwise a single missing
device would show up again as a different device type, emulator flag, ...

Rows are attributed to scenarios exactly through label_transactions.csv
(generator 1.2.0+), or, for older datasets without it, through the label
windows in labels.csv: a row belongs to a scenario when its owner is the
labelled entity and its business date falls inside the window.

Run:
    python -m app.scripts.audit_scenario_artefacts sample_data/Small
    python -m app.scripts.audit_scenario_artefacts <dir> --markdown report.md --json report.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from app.scripts.generate_raw_dataset import HIGH_RISK_COUNTRIES, JAKARTA

# A group smaller than this is reported as insufficient rather than judged.
MIN_ROWS = 20
# How far beyond the sampling-noise bound a distance must be to count.
MARGIN = 0.10

# What any scenario legitimately changes: what is done, in which direction,
# through which channel, and for how much.
BEHAVIOUR = frozenset({"direction", "channel", "transaction_type", "amount_magnitude"})
ROUNDNESS = frozenset({"amount_round_thousand", "amount_round_million", "amount_last_digit"})

# Views that are part of a pattern's definition, per (pattern, kind). Kinds:
# positive, look-alike, boundary. Anything not listed must look like background.
DEFINING: dict[tuple[str, str], frozenset[str]] = {
    ("P06", "positive"): ROUNDNESS,
    ("P06", "look-alike"): ROUNDNESS,  # round float top-ups, by construction
    ("P08", "positive"): frozenset({"counterparty_country"}),
    ("P08", "look-alike"): frozenset({"counterparty_country"}),  # cross-border support
    ("P04", "look-alike"): frozenset({"payday"}),  # payroll on payday
    ("P09", "positive"): frozenset({"device_shared"}),
    ("P11", "positive"): frozenset({"device_shared", "counterparty_kind"}),
    ("ATO", "positive"): frozenset({"device_recently_first_seen"}),
    # One look-alike kind is "a new phone on payday", so payday is its story.
    ("ATO", "look-alike"): frozenset({"device_recently_first_seen", "payday"}),
}
# Views that describe the device rather than the row. Rows of one scenario
# share their device, so these are judged on the number of distinct devices,
# not rows: 150 rows from 25 phones are 25 observations of a device type.
DEVICE_VIEWS = frozenset({"device_type", "device_emulator", "device_rooted", "device_shared",
                          "device_recently_first_seen"})
# Likewise the rows of one scenario share their dates and cluster in time: a
# burst of ten payments on one afternoon is one observation of a weekday, and
# the eight dates of a fixed-cadence sequence all follow from its first. Day
# views count distinct entities (one scenario each); hour counts distinct
# (entity, date, hour). A per-pattern group is then judged conservatively,
# while the pooled groups, with hundreds of scenarios, still catch a
# systematic date difference (1.1.0's scenarios avoided paydays).
DAY_VIEWS = frozenset({"weekday", "payday"})
KIND_MARKERS = (("-POS-", "positive"), ("-EDGE-", "look-alike"), ("-BOUND-", "boundary"))


def defining_views(pattern: str, kind: str) -> frozenset[str]:
    views = BEHAVIOUR | DEFINING.get((pattern, kind), frozenset())
    if kind == "boundary":
        # Exactly at a threshold, so exactly round, by construction.
        views = views | ROUNDNESS
    return views


def _kind(scenario_id: str) -> str | None:
    return next((kind for marker, kind in KIND_MARKERS if marker in scenario_id), None)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone(JAKARTA)
    except ValueError:
        return None


def _amount(value: str) -> float | None:
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if amount > 0 else None


@dataclass
class ViewResult:
    view: str
    tvd: float
    noise: float
    defining: bool
    flagged: bool
    # Largest differences, as (category, group share, background share).
    top: list[tuple[str, float, float]] = field(default_factory=list)


@dataclass
class GroupResult:
    group: str
    pattern: str
    kind: str
    rows: int
    judged: bool
    views: list[ViewResult]

    @property
    def flagged(self) -> list[ViewResult]:
        return [v for v in self.views if v.flagged]


@dataclass
class AuditResult:
    dataset: str
    generator_version: str
    attribution: str
    background_rows: int
    groups: list[GroupResult]

    @property
    def flagged(self) -> list[tuple[str, ViewResult]]:
        return [(g.group, v) for g in self.groups for v in g.flagged]


class _Context:
    """Lookups the views need: device facts and who uses each device."""

    def __init__(self, dataset: Path, transactions: list[dict[str, str]]) -> None:
        self.owner = {a["source_account_id"]: a["owner_source_id"] for a in _read(dataset / "accounts.csv")}
        self.devices = {d["source_device_id"]: d for d in _read(dataset / "devices.csv")}
        users: dict[str, set[str]] = defaultdict(set)
        for row in transactions:
            owner = self.owner.get(row["source_account_id"])
            if row["source_device_id"] and owner:
                users[row["source_device_id"]].add(owner)
        self.device_users = {device: len(owners) for device, owners in users.items()}


def _views(ctx: _Context) -> dict[str, Callable[[dict[str, str]], str]]:
    def hour(r):
        when = _parse_datetime(r["value_datetime"])
        return f"{when.hour:02d}" if when else "unparseable"

    def weekday(r):
        try:
            return date.fromisoformat(r["business_date"]).strftime("%a")
        except ValueError:
            return "unparseable"

    def payday(r):
        try:
            return "payday" if date.fromisoformat(r["business_date"]).day in (25, 26, 27, 1, 2) else "other"
        except ValueError:
            return "unparseable"

    def seconds_zero(r):
        when = _parse_datetime(r["value_datetime"])
        return "unparseable" if when is None else ("yes" if when.second == 0 else "no")

    def magnitude(r):
        amount = _amount(r["amount_original"])
        return f"1e{int(math.log10(amount))}" if amount else "invalid"

    def cents(r):
        amount = _amount(r["amount_original"])
        return "invalid" if amount is None else ("yes" if amount != int(amount) else "no")

    def last_digit(r):
        amount = _amount(r["amount_original"])
        return "invalid" if amount is None else str(int(amount) % 10)

    def round_to(unit):
        def view(r):
            amount = _amount(r["amount_original"])
            return "invalid" if amount is None else ("yes" if int(amount) % unit == 0 else "no")
        return view

    def counterparty_kind(r):
        ref = r["counterparty_reference"]
        return "missing" if not ref else ref.split("-", 1)[0]

    def counterparty_country(r):
        country = r["counterparty_country"]
        if not country:
            return "missing"
        return country if country == "ID" else ("listed" if country in HIGH_RISK_COUNTRIES else "foreign")

    # Conditional views return None for rows they do not apply to.
    def merchant_on_payment(r):
        if r["transaction_type"] != "MERCHANT_PAYMENT":
            return None
        return "present" if r["source_merchant_id"] else "missing"

    def device_attr(name):
        def view(r):
            device = ctx.devices.get(r["source_device_id"])
            return device[name] if device else None
        return view

    def device_recent(r):
        device = ctx.devices.get(r["source_device_id"])
        when = _parse_datetime(r["value_datetime"])
        if not device or when is None:
            return None
        first_seen = _parse_datetime(device["first_seen"])
        return "within 7 days" if first_seen and when - first_seen <= timedelta(days=7) else "older"

    def device_shared(r):
        users = ctx.device_users.get(r["source_device_id"], 0)
        return None if users == 0 else ("4+" if users >= 4 else str(users))

    def ip_range(r):
        return ".".join(r["ip_address"].split(".")[:3]) if r["ip_address"] else "missing"

    return {
        "hour": hour,
        "weekday": weekday,
        "payday": payday,
        "seconds_zero": seconds_zero,
        "direction": lambda r: r["direction"],
        "channel": lambda r: r["channel"],
        "transaction_type": lambda r: r["transaction_type"],
        "amount_magnitude": magnitude,
        "amount_has_cents": cents,
        "amount_last_digit": last_digit,
        "amount_round_thousand": round_to(1_000),
        "amount_round_million": round_to(1_000_000),
        "currency": lambda r: "IDR" if r["currency_original"] == "IDR" else "other",
        "counterparty_missing": lambda r: "yes" if not r["counterparty_reference"] else "no",
        "counterparty_kind": counterparty_kind,
        "counterparty_country": counterparty_country,
        "merchant_on_merchant_payment": merchant_on_payment,
        "device_missing": lambda r: "yes" if not r["source_device_id"] else "no",
        "device_type": device_attr("device_type"),
        "device_emulator": device_attr("is_emulator"),
        "device_rooted": device_attr("is_rooted"),
        "device_recently_first_seen": device_recent,
        "device_shared": device_shared,
        "ip_range": ip_range,
        "status_from_source": lambda r: r["status_from_source"],
    }


def _attribute(dataset: Path, transactions: list[dict[str, str]], owner: dict[str, str]) -> tuple[dict[int, str], str]:
    """Row index -> scenario_id, and how it was worked out."""
    labels = {l["scenario_id"]: l for l in _read(dataset / "labels.csv")}
    exact = dataset / "label_transactions.csv"
    if exact.exists():
        scenario_of = {r["source_transaction_reference"]: r["scenario_id"] for r in _read(exact)}
        return {i: scenario_of[r["source_transaction_reference"]] for i, r in enumerate(transactions)
                if r["source_transaction_reference"] in scenario_of}, "exact (label_transactions.csv)"
    windows: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for label in labels.values():
        if _kind(label["scenario_id"]) and label["window_start"]:
            windows[label["entity_source_id"]].append(
                (label["window_start"], label["window_end"], label["scenario_id"])
            )
    attributed = {}
    for i, row in enumerate(transactions):
        for start, end, scenario_id in windows.get(owner.get(row["source_account_id"], ""), ()):
            if start <= row["business_date"] <= end:
                attributed[i] = scenario_id
                break
    return attributed, "label windows (no label_transactions.csv: older generator)"


def _distribution(values: Counter[str]) -> dict[str, float]:
    total = sum(values.values())
    return {k: v / total for k, v in values.items()} if total else {}


def _compare(group: Counter[str], background: dict[str, float], n: int) -> tuple[float, float, list]:
    p = _distribution(group)
    categories = set(p) | set(background)
    tvd = 0.5 * sum(abs(p.get(c, 0.0) - background.get(c, 0.0)) for c in categories)
    # How far a random background sample of the same size would drift by
    # chance: each category's share deviates by about sigma_k, whose expected
    # absolute value is sigma_k * sqrt(2/pi). The bound is that mean plus three
    # standard deviations. (A per-category three-sigma sum is far too loose
    # for a view with many categories, e.g. hour.)
    sigmas = [math.sqrt(q * (1 - q) / n) for q in background.values()]
    mean = 0.5 * math.sqrt(2 / math.pi) * sum(sigmas)
    spread = 0.5 * math.sqrt((1 - 2 / math.pi) * sum(sigma * sigma for sigma in sigmas))
    noise = mean + 3 * spread
    top = sorted(((c, round(p.get(c, 0.0), 4), round(background.get(c, 0.0), 4)) for c in categories),
                 key=lambda t: -abs(t[1] - t[2]))[:3]
    return tvd, noise, top


def audit(dataset: Path) -> AuditResult:
    transactions = _read(dataset / "transactions.csv")
    ctx = _Context(dataset, transactions)
    labels = {l["scenario_id"]: l for l in _read(dataset / "labels.csv")}
    attributed, attribution = _attribute(dataset, transactions, ctx.owner)
    views = _views(ctx)

    background_counts = {name: Counter() for name in views}
    group_counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: {name: Counter() for name in views})
    group_devices: dict[str, set[str]] = defaultdict(set)
    group_entities: dict[str, set[str]] = defaultdict(set)
    group_hours: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    group_meta: dict[str, tuple[str, str]] = {}
    for i, row in enumerate(transactions):
        scenario_id = attributed.get(i)
        values = {name: value for name, view in views.items() if (value := view(row)) is not None}
        if scenario_id is None:
            for name, value in values.items():
                background_counts[name][value] += 1
            continue
        pattern = labels[scenario_id]["pattern_code"] if scenario_id in labels else scenario_id.split("-", 1)[0]
        kind = _kind(scenario_id) or "other"
        for key in (f"{pattern} {kind}", f"all {kind}s"):
            group_meta[key] = (pattern if not key.startswith("all ") else "ALL", kind)
            if row["source_device_id"]:
                group_devices[key].add(row["source_device_id"])
            entity = ctx.owner.get(row["source_account_id"], "")
            group_entities[key].add(entity)
            group_hours[key].add((entity, row["business_date"], values.get("hour", "")))
            for name, value in values.items():
                group_counts[key][name][value] += 1

    background = {name: _distribution(counts) for name, counts in background_counts.items()}
    # A pooled group mixes patterns, so only the views no pattern may change are judged there.
    never_defining = set(views) - BEHAVIOUR - ROUNDNESS - {v for vs in DEFINING.values() for v in vs}

    groups = []
    for key in sorted(group_counts, key=lambda k: (k.startswith("all "), k)):
        pattern, kind = group_meta[key]
        n = sum(group_counts[key]["direction"].values())
        judged = n >= MIN_ROWS
        results = []
        for name in views:
            defining = (name not in never_defining) if pattern == "ALL" else (name in defining_views(pattern, kind))
            applicable = sum(group_counts[key][name].values())
            if not applicable:
                continue
            if name in DEVICE_VIEWS:
                effective = min(applicable, len(group_devices[key]))
            elif name in DAY_VIEWS:
                effective = min(applicable, len(group_entities[key]))
            elif name == "hour":
                effective = min(applicable, len(group_hours[key]))
            else:
                effective = applicable
            tvd, noise, top = _compare(group_counts[key][name], background[name], effective)
            flagged = judged and effective >= MIN_ROWS and not defining and tvd > noise + MARGIN
            results.append(ViewResult(name, round(tvd, 4), round(noise, 4), defining, flagged, top))
        groups.append(GroupResult(key, pattern, kind, n, judged, results))

    manifest = json.loads((dataset / "manifest.json").read_text())
    return AuditResult(str(dataset), manifest.get("generator_version", "?"), attribution,
                       sum(background_counts["direction"].values()), groups)


def to_markdown(result: AuditResult) -> str:
    lines = [
        f"# Scenario-artefact audit — generator {result.generator_version}",
        "",
        f"Dataset `{result.dataset}`. Attribution: {result.attribution}. Background rows: {result.background_rows:,}.",
        f"A view is flagged when TVD > sampling-noise bound + {MARGIN} and it is not part of the pattern's",
        f"definition. Groups under {MIN_ROWS} rows are not judged.",
        "",
        f"**Flagged: {len(result.flagged)}**",
        "",
        "| Group | Rows | Flagged views (TVD) |",
        "|---|---|---|",
    ]
    for group in result.groups:
        if not group.judged:
            verdict = f"not judged (< {MIN_ROWS} rows)"
        elif group.flagged:
            verdict = ", ".join(f"**{v.view}** ({v.tvd:.2f})" for v in group.flagged)
        else:
            verdict = "clean"
        lines.append(f"| {group.group} | {group.rows:,} | {verdict} |")
    if result.flagged:
        lines += ["", "## Flagged detail", "", "| Group | View | TVD | Noise | Largest differences (group vs background) |",
                  "|---|---|---|---|---|"]
        for group_name, view in result.flagged:
            detail = "; ".join(f"{c}: {g:.1%} vs {b:.1%}" for c, g, b in view.top)
            lines.append(f"| {group_name} | {view.view} | {view.tvd:.2f} | {view.noise:.2f} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a generated dataset for scenario artefacts.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--markdown", type=Path, help="write the report as Markdown")
    parser.add_argument("--json", type=Path, help="write every distance as JSON")
    args = parser.parse_args()
    result = audit(args.dataset)
    report = to_markdown(result)
    print(report)
    if args.markdown:
        args.markdown.write_text(report, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    raise SystemExit(1 if result.flagged else 0)


if __name__ == "__main__":
    main()
