"""Point-in-time screen reconstruction.

Rebuilds what the screener would have said on a historical date using only
statements that had actually been filed by then, then measures real forward
returns. This is the test the stored workbooks could not support: it produces
both the picks and the control group of names that were evaluated and rejected.

Look-ahead control. Every financial statement is admitted on its `filingDate`,
not its period-end date — Corning's Q4 2025 covers the period ending
2025-12-31 but was not public until 2026-02-12, a 43-day lag. Filtering on
period end would hand the screener six weeks of unpublished results. Share
counts come from the enterprise-values endpoint, which carries no filing date,
so they are lagged by a fixed FILING_LAG_DAYS.

Known limitations, stated because they bound what the results mean:

1. Survivorship. The universe is the tickers the current screener evaluates.
   Companies that delisted between the test date and now are absent, which
   flatters every result. There is no fix without a historical constituent
   list.
2. Insider ownership has no history on this API. It is carried back unchanged,
   which is look-ahead for any company whose insiders since sold. `--no-insider`
   drops it and renormalises, and the two are compared in the output.
3. Industry and description come from today's profile, so a company that
   pivoted into a target theme is treated as always having been in it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import statistics as st
import sys
from typing import Any

from dotenv import load_dotenv

from config import TIERS
from screener.filters import passes_quality_floor, apply_sanity_filters, score_stock
from screener.fingerprint import compute_fingerprint_match, load_reference_stocks
from screener.fmp_client import FMPClient
from screener.insider_flow import compute_insider_flow, score_insider_flow
from screener.utils import compute_dilution, compute_revenue_metrics
from screener.winner_pattern import (
    compute_winner_pattern_score,
    extract_wps_inputs,
)

from . import prices

FILING_LAG_DAYS = 45     # applied to share counts, which carry no filing date
FWD_BARS = 252           # ~12 months
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "cache", "pit")

# Composite weights minus insider ownership, renormalised to 1.0.
WEIGHTS_NO_INSIDER = {
    "revenue_growth": 0.30 / 0.85, "gross_margin": 0.20 / 0.85,
    "dilution": 0.20 / 0.85, "revenue_acceleration": 0.15 / 0.85,
    "insider_ownership": 0.0,
}


def universe() -> list[dict[str, str]]:
    """Tickers the current screener evaluates, from the candidate log."""
    log_dir = os.path.join(os.path.dirname(CACHE), "..", "candidates")
    log_dir = os.path.normpath(log_dir)
    seen: dict[str, dict[str, str]] = {}
    for name in sorted(os.listdir(log_dir)) if os.path.isdir(log_dir) else []:
        if not name.endswith(".csv"):
            continue
        with open(os.path.join(log_dir, name)) as f:
            for r in csv.DictReader(f):
                seen.setdefault(r["ticker"], {
                    "ticker": r["ticker"], "name": r.get("name", ""),
                    "insider": r.get("insider_ownership_pct", ""),
                })
    return list(seen.values())


def fetch_insider(client: FMPClient, tickers: list[str]) -> dict[str, list]:
    """Form 4 history per ticker, cached separately from the statements."""
    import json
    cache = CACHE + "_insider"
    os.makedirs(cache, exist_ok=True)
    out: dict[str, list] = {}
    for i, t in enumerate(tickers, 1):
        path = os.path.join(cache, f"{t}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    out[t] = json.load(f)
                continue
            except (OSError, ValueError):
                pass
        data = client.get_insider_trades(t, limit=1000)
        out[t] = data
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except OSError:
            pass
        if i % 50 == 0:
            print(f"    insider {i}/{len(tickers)}", flush=True)
    return out


def fetch_fundamentals(client: FMPClient, tickers: list[str]
                       ) -> dict[str, dict[str, Any]]:
    """Deep statement history per ticker, cached to disk."""
    import json
    os.makedirs(CACHE, exist_ok=True)
    out: dict[str, dict[str, Any]] = {}
    for i, t in enumerate(tickers, 1):
        path = os.path.join(CACHE, f"{t}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    out[t] = json.load(f)
                continue
            except (OSError, ValueError):
                pass
        data = {
            "income": client.get_income_statements(t, quarters=40),
            "ev": client.get_enterprise_values(t, quarters=40),
            "annual": client.get_income_statements_annual(t, years=10),
            "balance": client.get_balance_sheet(t, periods=40, period="quarter"),
            "profile": client.get_profile(t),
        }
        out[t] = data
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except OSError:
            pass
        if i % 25 == 0:
            print(f"    fetched {i}/{len(tickers)}", flush=True)
    return out


def _filed_by(stmts: list[dict], asof: dt.date) -> list[dict]:
    """Statements public by `asof`, most recent first."""
    ok = []
    for s in stmts:
        fd = s.get("filingDate") or s.get("acceptedDate")
        if not fd:
            continue
        try:
            d = dt.date.fromisoformat(str(fd)[:10])
        except ValueError:
            continue
        if d <= asof:
            ok.append(s)
    return sorted(ok, key=lambda s: s.get("date", ""), reverse=True)


def _ev_by(evs: list[dict], asof: dt.date) -> list[dict]:
    """Share-count records assumed public FILING_LAG_DAYS after period end."""
    ok = []
    for e in evs:
        try:
            d = dt.date.fromisoformat(str(e.get("date", ""))[:10])
        except ValueError:
            continue
        if d + dt.timedelta(days=FILING_LAG_DAYS) <= asof:
            ok.append(e)
    return sorted(ok, key=lambda e: e.get("date", ""), reverse=True)


def tier_for(mkt_cap_m: float) -> str | None:
    for name, t in TIERS.items():
        if t["min"] <= mkt_cap_m <= t["max"]:
            return name
    return None


def evaluate(asof: dt.date, tickers: list[dict[str, str]],
             fund: dict[str, dict[str, Any]], bars: dict[str, prices.Bars],
             refs: list[dict], use_insider: bool,
             insider_trades: dict[str, list] | None = None) -> list[dict[str, Any]]:
    """Run the screen as of `asof`, returning every evaluated candidate."""
    rows: list[dict[str, Any]] = []
    for rec in tickers:
        t = rec["ticker"]
        b = bars.get(t)
        data = fund.get(t)
        if b is None or not data:
            continue
        i = b.index_on_or_after(asof)
        if i is None or i >= len(b) - 1:
            continue
        price = b.close[i]

        income = _filed_by(data.get("income") or [], asof)
        evs = _ev_by(data.get("ev") or [], asof)
        if len(income) < 5 or len(evs) < 2:
            continue

        rm = compute_revenue_metrics(income)
        dil = compute_dilution(evs[:12][::-1])
        shares = next((e.get("numberOfShares") for e in evs
                       if e.get("numberOfShares")), None)
        if not shares:
            continue
        mkt_cap_m = shares * price / 1e6
        tier = tier_for(mkt_cap_m)
        if tier is None:
            continue

        # Ownership percentage. Named distinctly from insider_trades: these are
        # different signals and an earlier revision shadowed one with the other,
        # silently disabling the transaction-flow scoring entirely.
        insider_pct = None
        if use_insider:
            try:
                insider_pct = float(rec["insider"]) if rec.get("insider") else None
            except ValueError:
                insider_pct = None

        m = {
            "revenue_growth_pct": rm["revenue_growth_pct"],
            "revenue_acceleration_pct": rm["revenue_acceleration_pct"],
            "gross_margin_pct": rm["gross_margin_pct"],
            "gross_margin_delta_yoy_pp": rm["gross_margin_delta_yoy_pp"],
            "dilution_3yr_pct": dil["dilution_3yr_pct"],
            "insider_ownership_pct": insider_pct,
            "market_cap_M": mkt_cap_m,
        }
        sane = apply_sanity_filters(m)
        passed = sane and passes_quality_floor(m)

        j = min(i + FWD_BARS, len(b) - 1)
        fwd = (b.close[j] - price) / price * 100
        row = {"asof": asof, "ticker": t, "tier": tier, "passed": passed,
               "fwd": fwd, "bars_forward": j - i, **m}
        if passed:
            row["composite"] = score_stock(
                m, None if use_insider else WEIGHTS_NO_INSIDER)
            row["fingerprint"], row["match"] = compute_fingerprint_match(m, refs)
            row["wps"] = _wps_at(asof, t, data, income, b, i, price, mkt_cap_m)
            if insider_trades is not None:
                flow = compute_insider_flow(
                    insider_trades.get(t, []), mkt_cap_m, asof)
                row["insider_flow"] = score_insider_flow(flow)
                row["insider_buyers"] = flow["insider_buyers"]
                row["insider_sellers"] = flow["insider_sellers"]
        rows.append(row)
    return rows


def _wps_at(asof: dt.date, ticker: str, data: dict[str, Any],
            income: list[dict], b: prices.Bars, i: int,
            price: float, mkt_cap_m: float) -> float | None:
    """WPS as it would have scored on `asof`.

    Quote and price-change inputs are rebuilt from the price series at that
    date rather than taken from today's endpoints, which is where the whole
    beaten-down and under-followed side of the score comes from. The profile
    (description, industry) is today's — an unavoidable look-ahead already
    noted in the module docstring, and the reason the tailwind sub-score
    should be read with that caveat.
    """
    annual = _filed_by(data.get("annual") or [], asof)
    balance = _filed_by(data.get("balance") or [], asof)
    if len(annual) < 2 or not balance:
        return None

    lo = max(0, i - 252)
    window = b.close[lo:i + 1]
    if len(window) < 30:
        return None
    quote = {"price": price, "yearHigh": max(window), "yearLow": min(window),
             "marketCap": mkt_cap_m * 1e6, "avgVolume": 0}
    six = i - 126
    price_change = {"6M": ((price - b.close[six]) / b.close[six] * 100)
                    if six >= 0 and b.close[six] > 0 else None}

    inputs = extract_wps_inputs({"ticker": ticker}, income, annual, balance,
                                quote, price_change, data.get("profile") or {})
    return compute_winner_pattern_score(inputs)["wps"]


def _bucket_stats(rows: list[dict[str, Any]], spy: float) -> str:
    f = [r["fwd"] for r in rows]
    if not f:
        return f"{0:>6}{'—':>10}{'—':>10}{'—':>8}{'—':>8}{'—':>10}"
    return (f"{len(rows):>6}{st.mean(f):>10.1f}{st.median(f):>10.1f}"
            f"{100*sum(1 for x in f if x>0)/len(f):>8.0f}%"
            f"{100*sum(1 for x in f if x>50)/len(f):>8.0f}%"
            f"{st.mean(f)-spy:>10.1f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-insider", action="store_true",
                    help="drop insider ownership (its history is not available)")
    ap.add_argument("--start", default="2021-06-30")
    ap.add_argument("--end", default="2025-09-01")
    args = ap.parse_args(argv)

    load_dotenv()
    key = os.getenv("FMP_API_KEY")
    if not key:
        print("FMP_API_KEY not set", file=sys.stderr)
        return 1

    uni = universe()
    print(f"universe: {len(uni)} tickers from the candidate log")
    client = FMPClient(key)
    print("  fetching statement history (cached after first run)...")
    fund = fetch_fundamentals(client, [u["ticker"] for u in uni])
    print("  fetching Form 4 history...")
    insider = fetch_insider(client, [u["ticker"] for u in uni])
    bars = prices.load_many([u["ticker"] for u in uni] + ["SPY"], rng="10y")
    spy_bars = bars.pop("SPY", None)
    if spy_bars is None:
        print("no SPY", file=sys.stderr)
        return 1
    refs = load_reference_stocks()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    dates, d = [], start
    while d <= end:
        dates.append(d)
        m = d.month + 3
        d = dt.date(d.year + (m > 12), (m - 1) % 12 + 1, 1)

    all_rows: list[dict[str, Any]] = []
    for asof in dates:
        rows = evaluate(asof, uni, fund, bars, refs, not args.no_insider,
                        insider_trades=insider)
        si = spy_bars.index_on_or_after(asof)
        if si is None:
            continue
        sj = min(si + FWD_BARS, len(spy_bars) - 1)
        spy = (spy_bars.close[sj] - spy_bars.close[si]) / spy_bars.close[si] * 100
        for r in rows:
            r["spy"] = spy
        all_rows += rows
        p = [r for r in rows if r["passed"]]
        print(f"  {asof}  evaluated {len(rows):>4}  passed {len(p):>4}  "
              f"SPY fwd {spy:+6.1f}%")

    if not all_rows:
        print("no rows produced")
        return 1
    _report(all_rows, args.no_insider)
    return 0


def _report(rows: list[dict[str, Any]], no_insider: bool) -> None:
    spy = st.mean([r["spy"] for r in rows])
    passed = [r for r in rows if r["passed"]]
    failed = [r for r in rows if not r["passed"]]

    print(f"\n{'='*72}")
    print("POINT-IN-TIME RECONSTRUCTION"
          + ("  (insider ownership excluded)" if no_insider else ""))
    print("="*72)
    print(f"{len(rows)} evaluations, {len({r['ticker'] for r in rows})} tickers, "
          f"{len({r['asof'] for r in rows})} dates")
    print(f"forward horizon {FWD_BARS} bars (~12m); SPY mean {spy:+.1f}%\n")
    print(f"{'group':<22}{'n':>6}{'mean':>10}{'median':>10}{'win':>8}"
          f"{'P(+50%)':>8}{'vs SPY':>10}")
    print("-"*72)
    print(f"{'PASSED the screen':<22}" + _bucket_stats(passed, spy))
    print(f"{'REJECTED':<22}" + _bucket_stats(failed, spy))
    print(f"{'all evaluated':<22}" + _bucket_stats(rows, spy))

    # Rank buckets within each date, among names that passed.
    for key in ("composite", "fingerprint", "wps", "insider_flow"):
        by: dict[dt.date, list[dict]] = {}
        for r in passed:
            if r.get(key) is not None:
                by.setdefault(r["asof"], []).append(r)
        Q: list[list[dict]] = [[], [], [], []]
        top10: list[dict] = []
        for _, seg in sorted(by.items()):
            rk = sorted(seg, key=lambda r: -r[key])
            top10 += rk[:10]
            n = len(rk)
            for qi in range(4):
                Q[qi] += rk[int(qi * n / 4):int((qi + 1) * n / 4)]
        print(f"\n  ranked by {key}:")
        print(f"  {'top 10 picks':<20}" + _bucket_stats(top10, spy))
        for qi, lab in enumerate(("Q1 (best)", "Q2", "Q3", "Q4 (worst)")):
            if Q[qi]:
                print(f"  {lab:<20}" + _bucket_stats(Q[qi], spy))

        # A pooled quartile spread can come from a handful of good dates, so
        # count how many individual dates actually order correctly.
        wins = tot = 0
        for _, seg in sorted(by.items()):
            if len(seg) < 8:
                continue
            rk = sorted(seg, key=lambda r: -r[key])
            q = len(rk) // 4
            tot += 1
            wins += st.median([r["fwd"] for r in rk[:q]]) > \
                st.median([r["fwd"] for r in rk[-q:]])
        print(f"  -> Q1 beat Q4 on median in {wins}/{tot} dates")

        # And whether the ordering is really a size effect in disguise.
        cap_med = st.median([r["market_cap_M"] for r in passed])
        for half, sel in (("small", lambda r: r["market_cap_M"] <= cap_med),
                          ("large", lambda r: r["market_cap_M"] > cap_med)):
            sub = [r for r in passed if sel(r) and r.get(key) is not None]
            if len(sub) < 8:
                continue
            rk = sorted(sub, key=lambda r: -r[key])
            q = len(rk) // 4
            spread = (st.median([r["fwd"] for r in rk[:q]])
                      - st.median([r["fwd"] for r in rk[-q:]]))
            print(f"     within {half} caps (n={len(rk)}): "
                  f"Q1-Q4 median spread {spread:+.1f}pp")


if __name__ == "__main__":
    raise SystemExit(main())
