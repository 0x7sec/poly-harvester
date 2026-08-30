"""
Collector: pull real Polymarket 5m BTC Up/Down market trade data over a window,
and pair it with Binance 1s BTC ticks. Produces a JSON file the backtest can use
to measure the ACTUAL complete-set arbitrage against real book prices (not a
synthetic book).

For each 5m market window we capture:
  - the market's conditionId, UP/DOWN token ids, and resolution outcome
  - the trade tape (timestamp, side, price, size) for BOTH legs
  - the Binance BTC price at the window start and end (for the true outcome)

The complete-set arb measurement then asks: at each point in the window, what
was the best available UP bid + DOWN bid (the cheapest way to buy a complete
set)? If that sum was < $1.00 - fees, the arb was available. We measure the
frequency and size of that inefficiency over the real historical data.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Accept": "application/json"}


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_market_by_slug(slug: str) -> Optional[dict]:
    # Older (resolved) markets require closed=true; active ones closed=false.
    for closed in ("true", "false"):
        try:
            data = _get(f"https://gamma-api.polymarket.com/markets?slug={slug}&closed={closed}")
            if isinstance(data, list) and data:
                return data[0]
        except Exception:
            continue
    return None


def get_market_trades(condition_id: str, max_pages: int = 6) -> List[dict]:
    """Pulls the full trade tape for a market (paginated)."""
    trades: List[dict] = []
    offset = 0
    for _ in range(max_pages):
        try:
            batch = _get(
                f"https://data-api.polymarket.com/trades?market={condition_id}"
                f"&limit=500&offset={offset}"
            )
        except Exception:
            break
        if not batch:
            break
        trades.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
        time.sleep(0.1)
    return trades


def collect_windows(
    hours: float = 24.0,
    window_seconds: int = 300,
    max_windows: int = 200,
    btc_ticks: Optional[List[Tuple[float, float]]] = None,
) -> List[dict]:
    """
    Collects real Polymarket 5m market data for the past `hours`, paired with
    Binance BTC ticks for outcome verification.
    """
    now = int(time.time())
    start_boundary = now - (now % window_seconds)

    # Build a lookup of BTC price by timestamp for outcome verification.
    btc_by_ts: Dict[int, float] = {}
    if btc_ticks:
        for ts, price in btc_ticks:
            btc_by_ts[int(ts)] = price

    # Generate candidate window start timestamps (oldest first).
    n_windows = int(hours * 3600 / window_seconds)
    candidates = []
    for i in range(n_windows, 0, -1):
        ts = start_boundary - i * window_seconds
        candidates.append(ts)

    results: List[dict] = []
    for idx, ts in enumerate(candidates):
        if len(results) >= max_windows:
            break
        slug = f"btc-updown-5m-{ts}"
        market = get_market_by_slug(slug)
        if not market:
            continue
        cond = market.get("conditionId")
        raw_tokens = market.get("clobTokenIds") or "[]"
        # clobTokenIds is a JSON-encoded string, e.g. '["123...", "456..."]'.
        try:
            tokens = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
        except Exception:
            tokens = []
        if not cond or len(tokens) < 2:
            continue
        up_token, down_token = tokens[0], tokens[1]
        outcomes = market.get("outcomes") or ["Up", "Down"]

        # Pull the trade tape for both legs (single call by conditionId returns
        # both assets; we tag each by its outcome label).
        trades = get_market_trades(cond)
        if not trades:
            continue

        # Separate UP vs DOWN trades. The data-api trade records carry an
        # 'outcome' field ("Up"/"Down") and an 'asset' token id. We match on
        # outcome label first (robust), falling back to asset token id.
        up_label = (outcomes[0] or "Up").lower()
        down_label = (outcomes[1] or "Down").lower()
        up_trades, down_trades = [], []
        for t in trades:
            rec = {
                "ts": float(t.get("timestamp", 0)),
                "side": t.get("side"),
                "price": float(t.get("price", 0)),
                "size": float(t.get("size", 0)),
            }
            outcome = str(t.get("outcome", "")).lower()
            asset = str(t.get("asset", ""))
            if outcome == up_label or asset == up_token:
                up_trades.append(rec)
            elif outcome == down_label or asset == down_token:
                down_trades.append(rec)
        up_trades.sort(key=lambda x: x["ts"])
        down_trades.sort(key=lambda x: x["ts"])

        # Outcome from Binance ticks (window start vs end price).
        start_price = btc_by_ts.get(int(ts))
        end_price = btc_by_ts.get(int(ts + window_seconds))
        outcome = None
        if start_price and end_price:
            outcome = "UP" if end_price >= start_price else "DOWN"

        results.append({
            "slug": slug,
            "window_start": float(ts),
            "window_end": float(ts + window_seconds),
            "condition_id": cond,
            "up_token": up_token,
            "down_token": down_token,
            "outcomes": outcomes,
            "btc_start": start_price,
            "btc_end": end_price,
            "outcome": outcome,
            "up_trades": up_trades,
            "down_trades": down_trades,
        })
        if (idx + 1) % 10 == 0:
            print(f"  collected {len(results)} windows ({idx+1}/{len(candidates)} checked)")
        time.sleep(0.15)

    return results


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--window", type=int, default=300)
    p.add_argument("--max-windows", type=int, default=200)
    p.add_argument("--btc-csv", default="btc_1s_24h.csv")
    p.add_argument("--out", default="pm_real_data.json")
    args = p.parse_args()

    # Load BTC ticks.
    btc_ticks = []
    try:
        with open(args.btc_csv, "r", encoding="utf-8") as f:
            next(f)  # header
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ts, price = line.split(",")
                btc_ticks.append((float(ts), float(price)))
        print(f"Loaded {len(btc_ticks)} BTC ticks")
    except Exception as e:
        print(f"Warning: could not load BTC csv ({e}); outcome verification disabled")

    print(f"Collecting {args.hours}h of 5m Polymarket markets...")
    windows = collect_windows(
        hours=args.hours,
        window_seconds=args.window,
        max_windows=args.max_windows,
        btc_ticks=btc_ticks,
    )
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(windows, f)
    with_outcome = sum(1 for w in windows if w["outcome"])
    with_trades = sum(1 for w in windows if w["up_trades"] or w["down_trades"])
    print(f"\nDONE: {len(windows)} windows -> {args.out}")
    print(f"  with outcome: {with_outcome}, with trades: {with_trades}")


if __name__ == "__main__":
    main()
