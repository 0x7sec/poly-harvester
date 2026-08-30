"""
Real-book complete-set arbitrage measurement.

Reads the collected Polymarket 5m market data (pm_real_data.json) and measures
the ACTUAL complete-set arbitrage opportunity against real historical trade
prices — NOT a synthetic book.

For each market window, we reconstruct the trade tape for the UP and DOWN legs
and ask: at each moment, what was the cheapest way to buy a complete set
(1 UP + 1 DOWN)? We approximate the "best buy price" for each leg from the
trade tape (the most recent trade price at or below which we could have
filled, i.e. the last printed price). The complete-set cost at time t is:

    cost(t) = last_up_price(t) + last_down_price(t)

If cost(t) < $1.00 - fees, a complete set was buyable at a profit. We measure:
  - the fraction of time the arb was available
  - the average edge when available
  - the max edge
  - the total realizable profit at a given clip size

This tells us whether the $0.89 avg combined cost the live bot achieves is
realistic, and how often the inefficiency actually appears.
"""
from __future__ import annotations

import json
from typing import List, Optional, Tuple


def _last_price_before(trades: List[dict], t: float) -> Optional[float]:
    """Most recent trade price at or before time t (the price we could fill at)."""
    last = None
    for tr in trades:  # trades are sorted by ts ascending
        if tr["ts"] <= t:
            last = tr["price"]
        else:
            break
    return last


def measure_window_arb(
    window: dict,
    fee_bps: float = 0.0,
    sample_step: float = 10.0,
) -> dict:
    """
    Measures the complete-set arb availability for a single market window using
    the real trade tape.

    Returns a dict with availability stats and the per-sample cost series.
    """
    up_trades = window.get("up_trades", [])
    down_trades = window.get("down_trades", [])
    w_start = window["window_start"]
    w_end = window["window_end"]

    # Sample the window at `sample_step` intervals.
    samples = []
    t = w_start
    while t <= w_end:
        up_p = _last_price_before(up_trades, t)
        down_p = _last_price_before(down_trades, t)
        if up_p is not None and down_p is not None:
            cost = up_p + down_p
            fee = (fee_bps / 10000.0) * cost
            net_cost = cost + fee
            edge = 1.0 - net_cost
            samples.append({
                "t": t,
                "up_price": up_p,
                "down_price": down_p,
                "combined_cost": cost,
                "net_cost": net_cost,
                "edge": edge,
                "arb_available": edge > 0,
            })
        t += sample_step

    if not samples:
        return {
            "slug": window.get("slug"),
            "samples": 0,
            "arb_available_pct": 0.0,
            "avg_edge_when_available": 0.0,
            "max_edge": 0.0,
            "avg_combined_cost": 0.0,
            "min_combined_cost": 1.0,
            "realizable_profit_25sh": 0.0,
        }

    n = len(samples)
    arb_samples = [s for s in samples if s["arb_available"]]
    arb_pct = len(arb_samples) / n * 100.0
    avg_edge = (sum(s["edge"] for s in arb_samples) / len(arb_samples)) if arb_samples else 0.0
    max_edge = max(s["edge"] for s in samples)
    avg_cost = sum(s["combined_cost"] for s in samples) / n
    min_cost = min(s["combined_cost"] for s in samples)
    # Realizable profit at 25 shares per clip, assuming we take the arb whenever
    # it's available (each sample = one clip opportunity).
    realizable_25 = sum(s["edge"] for s in arb_samples) * 25.0

    return {
        "slug": window.get("slug"),
        "samples": n,
        "arb_available_pct": round(arb_pct, 2),
        "avg_edge_when_available": round(avg_edge, 4),
        "max_edge": round(max_edge, 4),
        "avg_combined_cost": round(avg_cost, 4),
        "min_combined_cost": round(min_cost, 4),
        "realizable_profit_25sh": round(realizable_25, 2),
    }


def run_real_book_backtest(
    data_path: str = "pm_real_data.json",
    fee_bps: float = 0.0,
    sample_step: float = 10.0,
    clip_shares: float = 25.0,
) -> dict:
    """
    Runs the real-book complete-set arb measurement over all collected windows.
    """
    with open(data_path, "r", encoding="utf-8") as f:
        windows = json.load(f)

    per_window = []
    total_arb_profit = 0.0
    total_samples = 0
    total_arb_samples = 0
    cost_samples = []
    for w in windows:
        m = measure_window_arb(w, fee_bps=fee_bps, sample_step=sample_step)
        per_window.append(m)
        total_samples += m["samples"]
        total_arb_samples += int(m["arb_available_pct"] / 100.0 * m["samples"])
        total_arb_profit += m["realizable_profit_25sh"] * (clip_shares / 25.0)
        if m["samples"]:
            cost_samples.append(m["avg_combined_cost"])

    windows_with_data = [m for m in per_window if m["samples"] > 0]
    avg_arb_pct = (
        sum(m["arb_available_pct"] for m in windows_with_data) / len(windows_with_data)
        if windows_with_data else 0.0
    )
    avg_cost_overall = (
        sum(cost_samples) / len(cost_samples) if cost_samples else 0.0
    )

    return {
        "status": "SUCCESS",
        "source": "real Polymarket trade tape",
        "windows_total": len(windows),
        "windows_with_data": len(windows_with_data),
        "fee_bps": fee_bps,
        "sample_step_sec": sample_step,
        "clip_shares": clip_shares,
        # Aggregate complete-set arb stats
        "avg_arb_available_pct": round(avg_arb_pct, 2),
        "avg_combined_cost": round(avg_cost_overall, 4),
        "total_realizable_arb_profit": round(total_arb_profit, 2),
        "per_window": per_window,
        "verdict": _verdict(avg_arb_pct, avg_cost_overall, total_arb_profit, len(windows_with_data), clip_shares),
    }


def _verdict(arb_pct: float, avg_cost: float, profit: float, n_windows: int, clip_shares: float) -> str:
    if n_windows == 0:
        return "NO DATA"
    parts = [
        f"complete-set arb available {arb_pct:.1f}% of sampled time across {n_windows} windows",
        f"avg combined cost ${avg_cost:.3f}",
    ]
    if profit > 0:
        parts.append(f"realizable arb profit ${profit:.2f} at {clip_shares} sh/clip")
    else:
        parts.append("no net realizable arb at this clip size")
    return " | ".join(parts)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="pm_real_data.json")
    p.add_argument("--fee-bps", type=float, default=0.0)
    p.add_argument("--clip", type=float, default=25.0)
    args = p.parse_args()
    result = run_real_book_backtest(
        data_path=args.data,
        fee_bps=args.fee_bps,
        clip_shares=args.clip,
    )
    # Print a compact summary (per-window list can be long).
    summary = {k: v for k, v in result.items() if k != "per_window"}
    print(json.dumps(summary, indent=2))
    print(f"\nPer-window (first 15 of {len(result['per_window'])}):")
    for m in result["per_window"][:15]:
        print(f"  {m['slug']}: arb {m['arb_available_pct']}% | avg cost ${m['avg_combined_cost']} | min ${m['min_combined_cost']} | profit ${m['realizable_profit_25sh']}")
