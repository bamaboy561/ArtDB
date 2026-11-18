"""The share of fittings in sales and the salary coefficient of the motivation test.

The user's proposal (05.01.2027 - 04.04.2027): fittings are seven groups of the 1C
report, everything else, services included, is board material. The target share is
15%; at or above it the coefficient K is 1, below it K = 0.80 + share. Shares and K
are kept unrounded, and a month with no sales has no share at all rather than 0%.
In January and April only the days inside the test count.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re

import pandas as pd

# The seven groups of the proposal, as the 1C report names them under "Фурнитура".
FITTINGS_GROUPS = ("emaks", "hettich", "italiana ferramenta", "nuomi", "самет", "слорос", "слорос titus")
DEFAULT_SHARE_PLAN = 0.15
BASE_COEFFICIENT = 0.80
_GROUP_COLUMNS = ("путь категории", "группа", "category path", "group")
_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


@dataclass(frozen=True)
class MotivationTest:
    start: date
    end: date

    def covers(self, month: pd.Timestamp) -> tuple[date, date] | None:
        """The days of the month inside the test, or None when the month is outside it."""
        first = month.date().replace(day=1)
        last = (month + pd.offsets.MonthEnd(0)).date()
        start, end = max(first, self.start), min(last, self.end)
        return (start, end) if start <= end else None


TEST = MotivationTest(date(2027, 1, 5), date(2027, 4, 4))

MONTH_COLUMNS = [
    "month",
    "month_label",
    "days_label",
    "in_test",
    "fittings",
    "plate",
    "total",
    "share",
    "share_plan",
    "fittings_plan",
    "plan_execution",
    "coefficient",
]


def _key(value: object) -> str:
    return " ".join(re.sub(r"[^0-9a-zа-яё]+", " ", str(value or "").casefold().replace("ё", "е")).split())


def _is_fittings_group(text: object) -> bool:
    segments = [_key(part) for part in str(text or "").split(">")]
    return any(segment in FITTINGS_GROUPS or segment.startswith("слорос") for segment in segments)


def fittings_mask(sales: pd.DataFrame) -> pd.Series:
    """True for the sales lines of the seven fittings groups; the 1C group path decides."""
    columns = [column for column in sales.columns if str(column).strip().casefold() in _GROUP_COLUMNS]
    if not columns:
        # Older uploads keep no group path: only the top category is known then.
        category = sales.get("category", pd.Series("", index=sales.index))
        return category.map(lambda value: _key(value) == "фурнитура").astype(bool)
    mask = pd.Series(False, index=sales.index)
    for column in columns:
        values = sales[column]
        lookup = {value: _is_fittings_group(value) for value in values.dropna().unique()}
        mask |= values.map(lookup).fillna(False).astype(bool)
    return mask


def payout_coefficient(share: float | None, threshold: float = DEFAULT_SHARE_PLAN) -> float | None:
    """K of the proposal: 1 at or above the target share, 0.80 + share below it; None without a share."""
    if share is None or pd.isna(share):
        return None
    if share >= threshold:
        return 1.0
    return BASE_COEFFICIENT + max(float(share), 0.0)


def scope_fittings_plans(plans: pd.DataFrame, scope_salons: list[str], *, allow_network_fallback: bool) -> pd.DataFrame:
    """The fittings plan of the chosen salons per month.

    Salon plans add up in сом; their shares are averaged. The network's share is the
    target for any salon without one of its own, while its sum in сом counts only when
    every salon is chosen, as with the other plans.
    """
    columns = ["month_label", "share_plan", "fittings_plan"]
    if plans.empty or "fittings_share_plan" not in plans.columns:
        return pd.DataFrame(columns=columns)
    working = plans.assign(
        month_label=pd.to_datetime(plans["plan_month"], errors="coerce").dt.strftime("%Y-%m"),
        salon=plans["salon"].fillna("").astype(str).str.strip(),
    )
    # Only rows that plan fittings at all: a salon's plan of revenue alone says nothing about them.
    working = working[
        pd.to_numeric(working["fittings_share_plan"], errors="coerce").notna()
        | pd.to_numeric(working["fittings_revenue_plan"], errors="coerce").notna()
    ]
    scope = {str(salon).strip() for salon in scope_salons if str(salon).strip()}

    def by_month(rows: pd.DataFrame) -> pd.DataFrame:
        return rows.groupby("month_label").agg(
            share_plan=("fittings_share_plan", lambda values: pd.to_numeric(values, errors="coerce").mean()),
            fittings_plan=("fittings_revenue_plan", lambda values: pd.to_numeric(values, errors="coerce").sum(min_count=1)),
        )

    salons = by_month(working[working["salon"].isin(scope)])
    network = by_month(working[working["salon"].eq("")])
    months = salons.index.union(network.index)
    if months.empty:
        return pd.DataFrame(columns=columns)
    salons, network = salons.reindex(months), network.reindex(months)
    result = pd.DataFrame(
        {
            "share_plan": salons["share_plan"].fillna(network["share_plan"]),
            "fittings_plan": salons["fittings_plan"].fillna(network["fittings_plan"]) if allow_network_fallback else salons["fittings_plan"],
        },
        index=months,
    )
    result.index.name = "month_label"
    return result.reset_index()[columns]


def _days_label(start: date, end: date, month: pd.Timestamp) -> str:
    last = (month + pd.offsets.MonthEnd(0)).date()
    if start.day == 1 and end == last:
        return ""
    return f"{start.day}–{end.day} {_MONTHS[start.month - 1]}"


def monthly_structure(sales: pd.DataFrame, plans: pd.DataFrame | None = None, *, test: MotivationTest = TEST) -> pd.DataFrame:
    """Fittings and board material per month, the share against the plan and K."""
    if sales.empty or "date" not in sales.columns or "revenue" not in sales.columns:
        return pd.DataFrame(columns=MONTH_COLUMNS)
    dates = pd.to_datetime(sales["date"], errors="coerce")
    revenue = pd.to_numeric(sales["revenue"], errors="coerce").fillna(0.0)
    is_fittings = fittings_mask(sales)
    frame = pd.DataFrame({"date": dates.dt.date, "month": dates.dt.to_period("M").dt.to_timestamp(), "revenue": revenue, "fittings": is_fittings})
    frame = frame.dropna(subset=["month"])
    plan_lookup = (plans if plans is not None else pd.DataFrame(columns=["month_label", "share_plan", "fittings_plan"])).set_index("month_label")

    rows = []
    for month, lines in frame.groupby("month", sort=True):
        window = test.covers(month)
        if window is not None:
            lines = lines[(lines["date"] >= window[0]) & (lines["date"] <= window[1])]
        fittings = float(lines.loc[lines["fittings"], "revenue"].sum())
        total = float(lines["revenue"].sum())
        share = fittings / total if total > 0 else None
        label = month.strftime("%Y-%m")
        planned = plan_lookup.loc[label] if label in plan_lookup.index else None
        share_plan = float(planned["share_plan"]) if planned is not None and pd.notna(planned["share_plan"]) else DEFAULT_SHARE_PLAN
        fittings_plan = float(planned["fittings_plan"]) if planned is not None and pd.notna(planned["fittings_plan"]) else None
        rows.append(
            {
                "month": month,
                "month_label": label,
                "days_label": _days_label(window[0], window[1], month) if window is not None else "",
                "in_test": window is not None,
                "fittings": fittings,
                "plate": total - fittings,
                "total": total,
                "share": share,
                "share_plan": share_plan,
                "fittings_plan": fittings_plan,
                "plan_execution": fittings / fittings_plan if fittings_plan else None,
                "coefficient": payout_coefficient(share, share_plan),
            }
        )
    return pd.DataFrame(rows, columns=MONTH_COLUMNS)
