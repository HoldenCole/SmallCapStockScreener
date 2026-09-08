"""Re-score stored screener snapshots with current scoring, then bucket by rank.

Answers "does the new version rank better than the old one" on the six stored
workbooks, by recomputing each name's score from its stored raw metrics and
comparing forward returns across rank buckets.

What this CAN measure: re-ranking. The fingerprint changed from a min/max
envelope to nearest-archetype matching, which reorders the same set of names.

What this CANNOT measure, and no amount of re-running here will fix:

* The trough rule and the Vital Link tier. Both admit names the old screen
  rejected outright — a $26B incumbent with revenue down 11% never appears in
  a stored workbook, so there is nothing here to measure them against. Every
  stored row is a grower, which is also why the composite is unchanged for all
  of them: the margin-resilience credit only fires on a decline.
* The WPS changes. Recomputing WPS needs raw quarterly statements, which the
  workbooks do not store. Stored WPS values are used where present (the
  2026-06-06 file only) and the blend is renormalised where absent.

Usage:
    python -m backtest.rescore
"""

from __future__ import annotations

import datetime as dt
import glob
import math
import os
import statistics as st
from typing import Any, Callable

import openpyxl

from screener.fingerprint import compute_fingerprint_match, load_reference_stocks
from screener.filters import score_stock

from . import prices

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "output")
HIT_THRESHOLD = 25.0   # a "hit" is a forward return above this, in percent
FWD_BARS = 63          # ~3 months, held constant across snapshots


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_snapshots() -> list[dict[str, Any]]:
    """One row per (snapshot, ticker) with stored metrics and stored scores."""
    rows: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(OUTPUT_DIR,
                                              "screener_output_*.xlsx"))):
        tag = os.path.basename(path).split("_")[-1].split(".")[0]
        try:
            run_date = dt.date(int(tag[:4]), int(tag[4:6]), int(tag[6:8]))
        except (ValueError, IndexError):
            continue
        wb = openpyxl.load_workbook(path, data_only=True)
        for sheet in ("Nano Cap", "Small Cap", "Breakout", "Vital Link"):
            if sheet not in wb.sheetnames:
                continue
            sheet_rows = list(wb[sheet].iter_rows(values_only=True))
            hi = next((i for i, r in enumerate(sheet_rows)
                       if r and "Ticker" in [c for c in r if c]), None)
            if hi is None:
                continue
            hdr = list(sheet_rows[hi])
            for r in sheet_rows[hi + 1:]:
                if not r or not r[1] or not str(r[0]).isdigit():
                    continue
                d = dict(zip(hdr, r))
                # Workbook columns store fractions (0.30 = 30%); the scorers
                # take percentages.
                def pct(key: str) -> float | None:
                    v = _num(d.get(key))
                    return v * 100 if v is not None else None
                rows.append({
                    "date": run_date, "tier": sheet,
                    "ticker": str(d["Ticker"]).strip(),
                    "revenue_growth_pct": pct("Rev Growth YoY"),
                    "revenue_acceleration_pct": pct("Rev Accel"),
                    "gross_margin_pct": pct("Gross Margin"),
                    "dilution_3yr_pct": pct("Dilution 3yr"),
                    "insider_ownership_pct": pct("Insider %"),
                    "market_cap_M": _num(d.get("Mkt Cap ($M)")),
                    "stored_composite": _num(d.get("Composite")),
                    "stored_fingerprint": _num(d.get("Fingerprint")),
                    "stored_combined": _num(d.get("Combined")),
                    "stored_wps": _num(d.get("WPS")),
                })
    return rows


def attach_returns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add forward return over FWD_BARS and the matching SPY return."""
    tickers = sorted({r["ticker"] for r in rows})
    bars = prices.load_many(tickers + ["SPY"])
    spy = bars.get("SPY")
    out = []
    for r in rows:
        b = bars.get(r["ticker"])
        if b is None or spy is None:
            continue
        i = b.index_on_or_after(r["date"])
        if i is None or i >= len(b) - 1:
            continue
        j = min(i + FWD_BARS, len(b) - 1)
        si = spy.index_on_or_after(r["date"])
        if si is None:
            continue
        sj = min(si + FWD_BARS, len(spy) - 1)
        r = dict(r)
        r["fwd"] = (b.close[j] - b.close[i]) / b.close[i] * 100
        r["spy"] = (spy.close[sj] - spy.close[si]) / spy.close[si] * 100
        r["excess"] = r["fwd"] - r["spy"]
        out.append(r)
    return out


def rescore(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach current-version scores alongside the stored ones."""
    refs = load_reference_stocks()
    for r in rows:
        r["new_composite"] = score_stock(r)
        r["new_fingerprint"], r["match"] = compute_fingerprint_match(r, refs)
        wps = r.get("stored_wps")
        if wps is not None:
            r["new_combined"] = round(r["new_composite"] * 0.35
                                      + r["new_fingerprint"] * 0.25
                                      + wps * 0.40, 1)
        else:
            # No stored WPS: renormalise composite and fingerprint to 1.0 so
            # the number stays on the same 0-100 scale as the rows that have it.
            r["new_combined"] = round((r["new_composite"] * 0.35
                                       + r["new_fingerprint"] * 0.25) / 0.60, 1)
        r["old_combined"] = r.get("stored_combined")
    return rows


# ------------------------------------------------------------------ buckets

def _stats(seg: list[dict[str, Any]]) -> dict[str, float]:
    f = [r["fwd"] for r in seg]
    e = [r["excess"] for r in seg]
    return {
        "n": len(seg),
        "mean": st.mean(f), "median": st.median(f),
        "hit": 100.0 * sum(1 for x in f if x > HIT_THRESHOLD) / len(f),
        "excess": st.mean(e),
        "beat_spy": 100.0 * sum(1 for x in e if x > 0) / len(e),
    }


def _fmt(label: str, s: dict[str, float]) -> str:
    return (f"{label:<18}{s['n']:>6}{s['mean']:>9.1f}{s['median']:>9.1f}"
            f"{s['hit']:>8.0f}%{s['excess']:>10.1f}{s['beat_spy']:>9.0f}%")


HEADER = (f"{'bucket':<18}{'n':>6}{'mean':>9}{'median':>9}{'hit':>9}"
          f"{'vs SPY':>10}{'beat':>10}")


def buckets_by_snapshot(rows: list[dict[str, Any]], key: str
                        ) -> dict[str, list[dict[str, Any]]]:
    """Rank within each snapshot, then pool each bucket across snapshots.

    Ranking within a snapshot rather than across the pool matters: score levels
    drift between runs, so a pooled ranking would sort by run date as much as
    by quality.
    """
    out: dict[str, list[dict[str, Any]]] = {
        "top 10 picks": [], "top 10%": [],
        "Q1 (best)": [], "Q2": [], "Q3": [], "Q4 (worst)": [],
    }
    by_date: dict[dt.date, list[dict[str, Any]]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r)

    for _, seg in sorted(by_date.items()):
        ranked = sorted(seg, key=lambda r: -(r[key] if r[key] is not None else -1))
        n = len(ranked)
        out["top 10 picks"] += ranked[:10]
        out["top 10%"] += ranked[:max(1, round(n * 0.10))]
        q = n / 4.0
        for qi, name in enumerate(("Q1 (best)", "Q2", "Q3", "Q4 (worst)")):
            out[name] += ranked[math.floor(qi * q):math.floor((qi + 1) * q)]
    return out


def report(rows: list[dict[str, Any]], key: str, title: str) -> None:
    print(f"\n{title}")
    print("=" * 72)
    print(HEADER)
    print("-" * 72)
    print(_fmt("whole sample", _stats(rows)))
    for name, seg in buckets_by_snapshot(rows, key).items():
        if seg:
            print(_fmt(name, seg and _stats(seg)))


def main() -> int:
    rows = rescore(attach_returns(load_snapshots()))
    if not rows:
        print("no rows")
        return 1
    dates = sorted({r["date"] for r in rows})
    print(f"{len(rows)} rows, {len({r['ticker'] for r in rows})} tickers, "
          f"{len(dates)} snapshots ({dates[0]} to {dates[-1]})")
    print(f"forward horizon: {FWD_BARS} bars (~3 months); "
          f"hit = return above +{HIT_THRESHOLD:.0f}%")
    print(f"SPY over the same windows: mean {st.mean([r['spy'] for r in rows]):+.1f}%")

    report(rows, "old_combined", "OLD scoring (stored Combined)")
    report(rows, "new_combined", "NEW scoring (archetype fingerprint)")
    report(rows, "new_fingerprint", "NEW fingerprint alone")

    print("\nPER-SNAPSHOT: top quartile minus bottom quartile, hit rate")
    print("=" * 72)
    print(f"{'snapshot':<14}{'n':>5}{'old':>10}{'new':>10}{'fingerprint':>14}")
    print("-" * 72)
    for d in dates:
        seg = [r for r in rows if r["date"] == d]
        cells = []
        for key in ("old_combined", "new_combined", "new_fingerprint"):
            ranked = sorted(seg, key=lambda r: -(r[key] if r[key] is not None else -1))
            q = max(1, len(ranked) // 4)
            hi = 100.0 * sum(1 for r in ranked[:q] if r["fwd"] > HIT_THRESHOLD) / q
            lo = 100.0 * sum(1 for r in ranked[-q:] if r["fwd"] > HIT_THRESHOLD) / q
            cells.append(hi - lo)
        print(f"{str(d):<14}{len(seg):>5}{cells[0]:>+10.0f}{cells[1]:>+10.0f}"
              f"{cells[2]:>+14.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
