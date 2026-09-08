"""Full-universe candidate logging.

The screener evaluates every ticker in a tier but only ever writes out the top
N, so each run discards the evidence needed to answer the question that matters:
what separates the names that go on to run from the ones that don't.

Analysing only the names that passed cannot answer it. Every survivor already
clears the quality floor, so their feature distributions are truncated, and
within-sample slopes on a truncated variable go flat or invert — which is
exactly what the stored workbooks show (composite scores negatively against
forward return in 6 of 6 snapshots). The rejected names are the control group.

This module records every candidate with its raw metrics and its outcome. Rows
carry ticker and run date, so forward returns can be joined from price history
later; nothing here needs to know how a name performed.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "candidates",
)

FIELDS = [
    "run_date", "tier", "ticker", "name", "sector", "industry",
    "market_cap_M", "price",
    # raw metrics — the screener's actual inputs
    "revenue_growth_pct", "revenue_acceleration_pct", "gross_margin_pct",
    "gross_margin_delta_yoy_pp", "dilution_3yr_pct", "insider_ownership_pct",
    # scores, blank when the candidate was rejected before scoring
    "composite", "fingerprint", "wps", "insider_flow", "raw_combined",
    "run_maturity",
    "combined",
    # outcome
    "outcome", "reject_reason", "reconciliation_flags",
]

# Outcomes, coarsest first.
REJECTED_SANITY = "rejected_sanity"
REJECTED_QUALITY = "rejected_quality_floor"
REJECTED_DATA = "rejected_data_basis"
SCORED = "scored"
SELECTED = "selected"


class CandidateLog:
    """Accumulates one row per evaluated ticker for a single run."""

    def __init__(self, run_date: dt.date | None = None) -> None:
        self.run_date = run_date or dt.date.today()
        self._rows: list[dict[str, Any]] = []
        self._index: dict[tuple[str, str], dict[str, Any]] = {}

    def record(self, tier: str, ticker: str, outcome: str,
               metrics: dict[str, Any] | None = None,
               row: dict[str, Any] | None = None,
               scores: dict[str, Any] | None = None,
               reject_reason: str = "") -> None:
        """Record (or update) one candidate.

        Called again for the same ticker — e.g. once when scored and again when
        it makes the final cut — the existing row is updated in place rather
        than duplicated.
        """
        metrics = metrics or {}
        row = row or {}
        scores = scores or {}
        key = (tier, ticker)

        rec = self._index.get(key)
        if rec is None:
            rec = {f: "" for f in FIELDS}
            rec["run_date"] = self.run_date.isoformat()
            rec["tier"] = tier
            rec["ticker"] = ticker
            self._rows.append(rec)
            self._index[key] = rec

        rec["name"] = row.get("companyName", rec.get("name", "")) or ""
        rec["sector"] = row.get("sector", rec.get("sector", "")) or ""
        rec["industry"] = row.get("industry", rec.get("industry", "")) or ""
        rec["outcome"] = outcome
        if reject_reason:
            rec["reject_reason"] = reject_reason

        for k in ("revenue_growth_pct", "revenue_acceleration_pct",
                  "gross_margin_pct", "gross_margin_delta_yoy_pp",
                  "dilution_3yr_pct", "insider_ownership_pct", "market_cap_M",
                  "reconciliation_flags"):
            v = metrics.get(k)
            if v is not None:
                rec[k] = v
        for k, v in scores.items():
            if k in FIELDS and v is not None:
                rec[k] = v

    def save(self, path: str | None = None) -> str | None:
        """Write the log to CSV. Returns the path, or None if nothing to write."""
        if not self._rows:
            return None
        os.makedirs(LOG_DIR, exist_ok=True)
        path = path or os.path.join(
            LOG_DIR, f"candidates_{self.run_date:%Y%m%d}.csv")
        try:
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(self._rows)
        except OSError as exc:
            logger.error("could not write candidate log: %s", exc)
            return None
        return path

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for r in self._rows:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        parts = [f"{k}={v}" for k, v in sorted(counts.items())]
        return f"{len(self._rows)} candidates ({', '.join(parts)})"


def load_all(log_dir: str | None = None) -> list[dict[str, Any]]:
    """Read every logged run into one list, newest files last.

    Numeric columns come back as floats; blanks become None.
    """
    log_dir = log_dir or LOG_DIR
    if not os.path.isdir(log_dir):
        return []
    out: list[dict[str, Any]] = []
    numeric = set(FIELDS) - {"run_date", "tier", "ticker", "name", "sector",
                             "industry", "outcome", "reject_reason",
                             "reconciliation_flags"}
    for name in sorted(os.listdir(log_dir)):
        if not name.endswith(".csv"):
            continue
        try:
            with open(os.path.join(log_dir, name)) as f:
                for rec in csv.DictReader(f):
                    for k in numeric:
                        v = rec.get(k, "")
                        rec[k] = float(v) if v not in ("", None) else None
                    out.append(rec)
        except (OSError, ValueError) as exc:
            logger.warning("skipping unreadable candidate log %s: %s", name, exc)
    return out
