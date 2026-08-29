"""
20-Minute Optimized Benchmark (aswfadq1555 Archetype) with $300 USDC Capital.
Targets 15-minute BTC markets, $11 micro-order sizing, 4%-5% complete-set spread,
and enforces an ~86% paired inventory completion ratio.
"""
import asyncio
import os
import sys
import time
import json
from typing import Dict, List

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__)))

from config import BotConfig
from main import PolymarketQuantEngine


async def run_20min_optimized_benchmark(duration_seconds: int = 1200, starting_capital: float = 300.0):
    print("=" * 80)
    print(f"🚀 STARTING 20-MINUTE OPTIMIZED BENCHMARK ($300.00 USDC CAPITAL)")
    print(f"   Archetype: aswfadq1555 (15-Min Market Making & Complete-Set Accumulation)")
    print(f"   Target Combined Cost: <= $0.960 (4.0% - 5.0% Guaranteed Complete-Set Spread)")
    print(f"   Order Size: 25 Shares (~$11.25 per leg) | Feeds: KuCoin WSS + Polymarket CLOB")
    print("=" * 80)

    config = BotConfig(
        dry_run=True,
        target_edge_per_share=0.040,  # 4.0% target complete-set edge
        max_combined_cost=0.960,       # Strict $0.96 ceiling
        order_size_shares=25.0,        # ~$11 per trade
        max_inventory_imbalance=100.0,
        inventory_risk_aversion=0.003,
        trade_log_file="benchmark_20min_trades.csv",
    )

    engine = PolymarketQuantEngine(config)

    cash_balance = starting_capital
    trade_count = 0
    start_time = time.time()

    # Start real-time feeds
    feed_kc = asyncio.create_task(engine.kucoin_feed.start())
    feed_pm = asyncio.create_task(engine.polymarket_feed.start())

    try:
        while time.time() - start_time < duration_seconds:
            await asyncio.sleep(1.0)
            elapsed = int(time.time() - start_time)

            # Update trade fills
            if engine.paper_engine.fill_history:
                while len(engine.paper_engine.fill_history) > trade_count:
                    fill = engine.paper_engine.fill_history[trade_count]
                    trade_count += 1
                    trade_cost = fill["price"] * fill["shares"]
                    cash_balance -= trade_cost

            # Progress log every 30 seconds
            if elapsed % 30 == 0 and elapsed > 0:
                kc_p = engine.kucoin_feed.current_price
                inv = engine.inventory.get_summary()
                q_up = engine.current_quotes.get("quote_up", 0.0)
                q_dn = engine.current_quotes.get("quote_down", 0.0)
                edge_pct = engine.current_quotes.get("projected_edge", 0.0) * 100.0
                realized_pnl = inv["realized_arb_pnl"]

                # Calculate paired ratio: (2 * merged_sets) / (2 * merged_sets + active_residual)
                total_shares_traded = (2.0 * inv["complete_sets_merged"]) + inv["up_shares"] + inv["down_shares"]
                paired_ratio = ((2.0 * inv["complete_sets_merged"]) / total_shares_traded * 100.0) if total_shares_traded > 0 else 0.0

                print(
                    f"[{elapsed//60:02d}:{elapsed%60:02d} / {duration_seconds//60:02d}:00] "
                    f"BTC: ${kc_p:,.2f} | "
                    f"Quotes: UP=${q_up:.2f} DN=${q_dn:.2f} (Edge: {edge_pct:.1f}%) | "
                    f"Fills: {trade_count} | Sets: {inv['complete_sets_merged']:.0f} | "
                    f"Paired Ratio: {paired_ratio:.1f}% | PnL: +${realized_pnl:.2f}"
                )

    finally:
        feed_kc.cancel()
        feed_pm.cancel()
        await engine.kucoin_feed.stop()
        await engine.polymarket_feed.stop()

    total_elapsed = time.time() - start_time
    final_inv = engine.inventory.get_summary()
    merged_sets = final_inv["complete_sets_merged"]
    realized_pnl = final_inv["realized_arb_pnl"]

    total_shares_traded = (2.0 * merged_sets) + final_inv["up_shares"] + final_inv["down_shares"]
    paired_ratio = ((2.0 * merged_sets) / total_shares_traded * 100.0) if total_shares_traded > 0 else 0.0
    avg_pnl_per_trade = (realized_pnl / trade_count) if trade_count > 0 else 0.0
    avg_pnl_per_set = (realized_pnl / merged_sets) if merged_sets > 0 else 0.0
    return_pct = (realized_pnl / starting_capital) * 100.0

    report = {
        "starting_capital": starting_capital,
        "duration_minutes": round(total_elapsed / 60.0, 2),
        "total_trades_executed": trade_count,
        "complete_sets_merged": merged_sets,
        "paired_completion_ratio_pct": round(paired_ratio, 2),
        "realized_profit_20m": round(realized_pnl, 2),
        "return_20m_pct": round(return_pct, 2),
        "avg_profit_per_trade": round(avg_pnl_per_trade, 4),
        "avg_profit_per_complete_set": round(avg_pnl_per_set, 4),
        "inventory_snapshot": final_inv,
    }

    with open("benchmark_20min_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4)

    print("\n" + "=" * 80)
    print("📈 20-MINUTE OPTIMIZED BENCHMARK PERFORMANCE REPORT")
    print("=" * 80)
    print(f"Starting Capital              : ${starting_capital:.2f} USDC")
    print(f"Duration                      : {total_elapsed/60.0:.1f} Minutes")
    print(f"Total Trades Executed         : {trade_count}")
    print(f"Complete Sets Merged          : {merged_sets:.0f} ($1.00 CTF redemptions)")
    print(f"Paired Inventory Ratio        : {paired_ratio:.1f}% (Target: ~86%)")
    print(f"Active Residual Inventory     : {final_inv['up_shares']} UP | {final_inv['down_shares']} DOWN")
    print(f"Net Realized Arbitrage Profit : +${realized_pnl:.2f} ({return_pct:+.2f}%)")
    print(f"Avg Profit per Trade          : +${avg_pnl_per_trade:.4f}")
    print(f"Avg Profit per Complete Set   : +${avg_pnl_per_set:.4f} (~{avg_pnl_per_set*100:.1f}% edge/set)")
    print("=" * 80)
    print(f"Detailed logs saved to: benchmark_20min_trades.csv and benchmark_20min_report.json")


if __name__ == "__main__":
    asyncio.run(run_20min_optimized_benchmark(duration_seconds=1200, starting_capital=300.0))
