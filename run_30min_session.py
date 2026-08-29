"""
30-Minute Forward Paper Trading Benchmark with $300 USDC Capital.
Runs real-time Binance WebSocket & Polymarket CLOB market making,
asynchronous complete-set accumulation, tracks individual trade PnL,
and exports comprehensive performance analytics.
"""
import asyncio
import os
import sys
import time
import json
from dataclasses import dataclass
from typing import List, Dict

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__)))

from config import BotConfig
from main import PolymarketQuantEngine


async def run_30min_benchmark(duration_seconds: int = 1800, starting_capital: float = 300.0):
    print("=" * 85)
    print(f"🚀 STARTING 30-MINUTE FORWARD PAPER TRADING BENCHMARK (${starting_capital:.2f} USDC CAPITAL)")
    print(f"   Duration: {duration_seconds // 60} Minutes ({duration_seconds}s)")
    print(f"   Strategy: Asynchronous Volatility Harvesting + Complete-Set Accumulation (aswfadq1555 Archetype)")
    print(f"   Target Complete-Set Cost Ceiling: <= $0.960 (4.0% - 5.0% Guaranteed Complete-Set Spread)")
    print(f"   Order Size: 20 Shares (~$9.50/leg) | Primary Feed: Binance 50ms Direct WebSocket")
    print("=" * 85)

    config = BotConfig(
        dry_run=True,
        target_edge_per_share=0.040,   # 4.0% target edge
        max_combined_cost=0.960,        # Strict $0.960 cost ceiling
        order_size_shares=20.0,         # ~$9.50 per trade leg
        max_inventory_imbalance=60.0,   # Max unhedged position cap
        daily_stop_loss_usd=25.0,       # $25 stop loss cap
        trade_log_file="benchmark_30min_trades.csv",
    )

    engine = PolymarketQuantEngine(config)

    cash_balance = starting_capital
    trade_count = 0
    start_time = time.time()

    # Start live real-time feeds
    feed_bn = asyncio.create_task(engine.binance_feed.start())
    feed_pm = asyncio.create_task(engine.polymarket_feed.start())

    try:
        while time.time() - start_time < duration_seconds:
            await asyncio.sleep(1.0)
            elapsed = int(time.time() - start_time)

            # Process any new fills
            if engine.paper_engine.fill_history:
                while len(engine.paper_engine.fill_history) > trade_count:
                    fill = engine.paper_engine.fill_history[trade_count]
                    trade_count += 1
                    trade_cost = fill["price"] * fill["shares"]
                    cash_balance -= trade_cost

            # Progress log every 30 seconds
            if elapsed % 30 == 0 and elapsed > 0:
                bn_p = engine.binance_feed.current_price
                vel = engine.binance_feed.get_velocity()
                inv = engine.inventory.get_summary()
                q_up = engine.current_quotes.get("quote_up", 0.0)
                q_dn = engine.current_quotes.get("quote_down", 0.0)
                sum_cost = engine.current_quotes.get("projected_cost", 0.0)
                edge_pct = engine.current_quotes.get("projected_edge", 0.0) * 100.0
                realized_pnl = inv["realized_arb_pnl"]

                # Calculate paired ratio: (2 * merged_sets) / (2 * merged_sets + active_residual)
                total_shares_traded = (2.0 * inv["complete_sets_merged"]) + inv["up_shares"] + inv["down_shares"]
                paired_ratio = ((2.0 * inv["complete_sets_merged"]) / total_shares_traded * 100.0) if total_shares_traded > 0 else 0.0

                print(
                    f"[{elapsed//60:02d}:{elapsed%60:02d} / {duration_seconds//60:02d}:00] "
                    f"BTC: ${bn_p:,.2f} ({vel:+.2f}$/s) | "
                    f"Quotes: UP=${q_up:.2f} DN=${q_dn:.2f} (Sum: ${sum_cost:.3f} | Edge: {edge_pct:.1f}%) | "
                    f"Fills: {trade_count} | Merged Sets: {inv['complete_sets_merged']:.0f} | "
                    f"Paired Ratio: {paired_ratio:.1f}% | PnL: +${realized_pnl:.2f}"
                )

    finally:
        feed_bn.cancel()
        feed_pm.cancel()
        await engine.binance_feed.stop()
        await engine.polymarket_feed.stop()

    total_elapsed = time.time() - start_time
    final_inv = engine.inventory.get_summary()
    merged_sets = final_inv["complete_sets_merged"]
    realized_pnl = final_inv["realized_arb_pnl"]
    net_pnl = final_inv["net_pnl"]

    total_shares_traded = (2.0 * merged_sets) + final_inv["up_shares"] + final_inv["down_shares"]
    paired_ratio = ((2.0 * merged_sets) / total_shares_traded * 100.0) if total_shares_traded > 0 else 0.0
    avg_pnl_per_trade = (realized_pnl / trade_count) if trade_count > 0 else 0.0
    avg_pnl_per_set = (realized_pnl / merged_sets) if merged_sets > 0 else 0.0
    return_30m_pct = (realized_pnl / starting_capital) * 100.0

    # Hourly and longer projections
    hourly_pnl = realized_pnl * (3600.0 / max(1.0, total_elapsed))
    daily_pnl = hourly_pnl * 24.0
    weekly_pnl = daily_pnl * 7.0
    monthly_pnl = daily_pnl * 30.0

    report = {
        "starting_capital": starting_capital,
        "duration_minutes": round(total_elapsed / 60.0, 2),
        "total_trades_executed": trade_count,
        "complete_sets_merged": merged_sets,
        "paired_completion_ratio_pct": round(paired_ratio, 2),
        "realized_profit_30m": round(realized_pnl, 2),
        "return_30m_pct": round(return_30m_pct, 2),
        "avg_profit_per_trade": round(avg_pnl_per_trade, 4),
        "avg_profit_per_complete_set": round(avg_pnl_per_set, 4),
        "projections": {
            "hourly_pnl": round(hourly_pnl, 2),
            "daily_pnl": round(daily_pnl, 2),
            "weekly_pnl": round(weekly_pnl, 2),
            "monthly_pnl": round(monthly_pnl, 2),
            "monthly_roi_pct": round((monthly_pnl / starting_capital) * 100.0, 1),
        },
        "inventory_snapshot": final_inv,
    }

    with open("benchmark_30min_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4)

    print("\n" + "=" * 85)
    print("📈 30-MINUTE FORWARD BENCHMARK PERFORMANCE REPORT")
    print("=" * 85)
    print(f"Starting Capital              : ${starting_capital:.2f} USDC")
    print(f"Duration                      : {total_elapsed/60.0:.1f} Minutes ({total_elapsed:.0f}s)")
    print(f"Total Trades Executed         : {trade_count}")
    print(f"Complete Sets Merged          : {merged_sets:.0f} ($1.00 CTF redemptions)")
    print(f"Paired Inventory Ratio        : {paired_ratio:.1f}% (Target: > 80%)")
    print(f"Active Residual Inventory     : {final_inv['up_shares']} UP | {final_inv['down_shares']} DOWN")
    print(f"Net Realized Arbitrage Profit : +${realized_pnl:.2f} ({return_30m_pct:+.2f}%)")
    print(f"Avg Profit per Trade          : +${avg_pnl_per_trade:.4f}")
    print(f"Avg Profit per Complete Set   : +${avg_pnl_per_set:.4f}")
    print("-" * 85)
    print("🔮 RUN-RATE PROJECTIONS ($300 Bankroll):")
    print(f"   • Hourly Projected PnL : +${hourly_pnl:.2f} / hr")
    print(f"   • Daily Projected PnL  : +${daily_pnl:.2f} / day")
    print(f"   • Weekly Projected PnL : +${weekly_pnl:.2f} / week")
    print(f"   • Monthly Projected PnL: +${monthly_pnl:.2f} / month (ROI: {report['projections']['monthly_roi_pct']}%)")
    print("=" * 85)
    print(f"Detailed logs saved to: benchmark_30min_trades.csv and benchmark_30min_report.json")


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 1800
    asyncio.run(run_30min_benchmark(duration_seconds=duration, starting_capital=300.0))
