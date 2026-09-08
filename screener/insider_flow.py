"""Insider transaction flow — what insiders are doing, not where they stand.

Ownership level is a position: it says how much of the company management
holds, and it changes slowly. Transaction flow is a decision, dated, and it
says what they did with their own money this quarter. The two can point in
opposite directions — a founder with 30% who has been selling every quarter,
or a new CEO with 1% buying in the open market.

Form 4 transaction codes matter more than they look. Across a sample of twelve
selected names the raw acquisition/disposition split is dominated by
compensation: 237 A-Award grants and 247 M-Exempt option exercises against 92
genuine P-Purchase open-market buys. On the sell side, 130 of the dispositions
are F-InKind — shares withheld to cover tax on vesting, which no one chose.
Counting on acquisitionOrDisposition would therefore measure payroll rather
than conviction.

So only two codes count here:

    P-Purchase — an insider spending their own money
    S-Sale     — an insider choosing to sell into the market

Everything else is compensation, tax mechanics, gifts or transfers, and is
ignored. Nothing here is netted against ownership level; the two are separate
inputs by design.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

BUY_CODES = {"P-Purchase"}
SELL_CODES = {"S-Sale"}

# Materiality. A director buying $10k is a gesture, not a position, and the
# unfiltered numbers are structurally lopsided: across 65,685 qualifying
# transactions the median sale is $131,546 against a median purchase of
# $22,578, and sells outnumber buys five to one. Netting raw dollars therefore
# reports net selling almost everywhere regardless of what anyone believes.
MIN_TRANSACTION_USD = 25_000.0

# A floor on the NET as a share of market cap was tested and rejected. It reads
# as obviously right — $589k of buying at Celanese's $4.9B is 0.012% and feels
# immaterial — but requiring 0.05% cut the signal from 16 of 18 dates to 13 and
# halved the size-controlled spread. Small net buying is informative provided
# the individual transactions are real, which the per-transaction minimum
# already ensures. Kept at zero, and documented so it is not "fixed" later.
MIN_NET_FRAC_MKTCAP = 0.0

# Weight on the selling side. Grouping net-sellers against names with no
# transactions at all showed almost no difference (+0.3% median against -0.4%),
# which argued for muting selling toward neutral. Ranking says the opposite and
# emphatically: at 0.5 the signal falls to 12 of 18 dates, and ignoring selling
# entirely INVERTS it — 3 of 18 and a -16.6pp spread.
#
# Both results are true and not in conflict. The grouping collapses magnitude,
# so it compares the average seller with the average non-seller and finds them
# alike. The ranking keeps magnitude, and heavy selling is genuinely worse than
# light selling. Selling stays at full weight.
SELL_WEIGHT = 1.0

# Net flow as a share of market cap, at which the score saturates. Insider
# buying is small in absolute terms even when it means something — a director
# putting $200k into a $500M company is 0.04% and is a real signal — so the
# scale has to be sensitive down at fractions of a percent.
SATURATION_FRAC = 0.005

# Breadth adjustment. Three insiders buying independently says more than one
# buying three times, so distinct participants move the score on their own.
BREADTH_POINTS = 6.0
BREADTH_CAP = 18.0


def _parse(d: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(d)[:10])
    except (ValueError, TypeError):
        return None


def compute_insider_flow(
    trades: list[dict[str, Any]],
    market_cap_m: float | None,
    asof: dt.date | None = None,
    months: int = 12,
) -> dict[str, Any]:
    """Net open-market insider buying over the trailing `months`.

    Gated on filingDate rather than transactionDate, since a Form 4 is not
    public until it is filed. `asof` defaults to today; passing it lets the
    point-in-time reconstruction ask what was knowable on a past date.
    """
    asof = asof or dt.date.today()
    start = asof - dt.timedelta(days=int(months * 30.44))

    buy_val = sell_val = 0.0
    buy_sh = sell_sh = 0.0
    buyers: set[str] = set()
    sellers: set[str] = set()

    for t in trades:
        filed = _parse(t.get("filingDate"))
        if filed is None or not (start <= filed <= asof):
            continue
        code = t.get("transactionType")
        shares = float(t.get("securitiesTransacted") or 0)
        price = float(t.get("price") or 0)
        if shares <= 0 or shares * price < MIN_TRANSACTION_USD:
            continue
        who = str(t.get("reportingCik") or t.get("reportingName") or "")
        value = shares * price
        if code in BUY_CODES:
            buy_val += value
            buy_sh += shares
            buyers.add(who)
        elif code in SELL_CODES:
            sell_val += value
            sell_sh += shares
            sellers.add(who)

    net_val = buy_val - sell_val * SELL_WEIGHT
    result: dict[str, Any] = {
        "insider_buy_value": buy_val,
        "insider_sell_value": sell_val,
        "insider_net_value": net_val,
        "insider_buyers": len(buyers),
        "insider_sellers": len(sellers),
        "insider_buy_shares": buy_sh,
        "insider_sell_shares": sell_sh,
        "insider_net_pct_mktcap": None,
        "insider_flow_months": months,
    }
    if market_cap_m and market_cap_m > 0:
        result["insider_net_pct_mktcap"] = net_val / (market_cap_m * 1e6) * 100
    return result


def score_insider_flow(flow: dict[str, Any]) -> float:
    """0-100. Fifty means no open-market activity either way.

    Deliberately centred rather than zero-based: most companies have no
    qualifying transactions in a given year, and silence is not bearish.
    """
    if not flow:
        return 50.0
    buyers = flow.get("insider_buyers") or 0
    sellers = flow.get("insider_sellers") or 0
    if buyers == 0 and sellers == 0:
        return 50.0

    pct = flow.get("insider_net_pct_mktcap")
    if pct is not None and abs(pct) < MIN_NET_FRAC_MKTCAP * 100:
        return 50.0
    score = 50.0
    if pct is not None:
        frac = max(-1.0, min(1.0, pct / (SATURATION_FRAC * 100)))
        score += frac * 32.0
    elif flow.get("insider_net_value", 0) != 0:
        # No market cap to normalise against — fall back to direction only.
        score += 16.0 if flow["insider_net_value"] > 0 else -16.0

    breadth = min(BREADTH_CAP, abs(buyers - sellers) * BREADTH_POINTS)
    score += breadth if buyers > sellers else -breadth

    return round(max(0.0, min(100.0, score)), 1)


def explain_insider_flow(flow: dict[str, Any]) -> str:
    b, s = flow.get("insider_buyers", 0), flow.get("insider_sellers", 0)
    if b == 0 and s == 0:
        return f"no open-market insider transactions in {flow.get('insider_flow_months', 12)}m"
    net = flow.get("insider_net_value", 0.0)
    pct = flow.get("insider_net_pct_mktcap")
    tail = f" ({pct:+.3f}% of cap)" if pct is not None else ""
    return (f"{b} buyer(s) / {s} seller(s), net ${net/1e6:+.2f}M{tail}"
            f" over {flow.get('insider_flow_months', 12)}m")
