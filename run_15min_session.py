"""
15-Minute Paper Trading Benchmark with $300 USDC Capital.
Runs real-time KuCoin & Polymarket market making, complete-set accumulation,
tracks individual trade PnL, and calculates weekly/monthly projections.
"""
import asyncio
import os
import sys
import time
import json
from dataclasses import dataclass
from typing import List

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__)))

from config import BotConfig
from main import PolymarketQuantEngine


@dataclass
class CompletedSetRecord:
    timestamp: float
    up_price: float
    down_price: float
    shares: float
    cost_per_set: float
    profit_per_set: float
    total_profit: float


async def run_15min_benchmark(duration_seconds: int = 900, starting_capital: float = 300.0):
    print("=" * 80)
    print(f"🚀 STARTING 15-MINUTE BENCHMARK WITH ${starting_capital:.2f} USDC CAPITAL")
    print(f"   Duration: {duration_seconds // 60} Minutes ({duration_seconds}s)")
    print(f"   Strategy: Polkadot-Frog Volatility Harvesting & Complete-Set Merging")
    print(f"   Data Feeds: KuCoin Public WSS (BTC-USDT) + Polymarket CLOB")
    print("=" * 80)

    config = BotConfig(
        dry_run=True,
        target_edge_per_share=0.015,
        max_combined_cost=0.985,
        order_size_shares=50.0,  # ~$25 per order leg (8.3% of capital per leg)
        trade_log_file="benchmark_15min_trades.csv",
    )

    engine = PolymarketQuantEngine(config)

    # Track capital and completed sets
    cash_balance = starting_capital
    completed_sets: List[CompletedSetRecord] = []
    trade_count = 0
    start_time = time.time()
    last_reported_minute = -1

    # Start feeds
    feed_kc = asyncio.create_task(engine.kucoin_feed.start())
    feed_pm = asyncio.create_task(engine.polymarket_feed.start())

    try:
        while time.time() - start_time < duration_seconds:
            await asyncio.sleep(1.0)
            elapsed = int(time.time() - start_time)
            current_minute = elapsed // 60

            # Check if any new fills occurred
            if engine.paper_engine.fill_history:
                # Process new fills since last check
                while len(engine.paper_engine.fill_history) > trade_count:
                    fill = engine.paper_engine.fill_history[trade_count]
                    trade_count += 1
                    trade_cost = fill["price"] * fill["shares"]
                    cash_balance -= trade_cost

            # Progress log every 30 seconds
            if elapsed % 30 == 0 and elapsed > 0:
                kc_p = engine.kucoin_feed.current_price
                vel = engine.kucoin_feed.get_velocity()
                inv = engine.inventory.get_summary()
                q_up = engine.current_quotes.get("quote_up", 0.0)
                q_dn = engine.current_quotes.get("quote_down", 0.0)
                edge_pct = engine.current_quotes.get("projected_edge", 0.0) * 100.0

                # Estimated active position value at mark price ($0.50 baseline)
                active_pos_val = (inv["up_shares"] * 0.50) + (inv["down_shares"] * 0.50)
                total_equity = cash_balance + active_pos_val + (inv["complete_sets_merged"] * 1.00)
                realized_pnl = inv["realized_arb_pnl"]

                print(
                    f"[{elapsed//60:02d}:{elapsed%60:02d} / {duration_seconds//60:02d}:00] "
                    f"BTC: ${kc_p:,.2f} | "
                    f"Quotes: UP=${q_up:.2f} DN=${q_dn:.2f} (Edge: {edge_pct:.1f}%) | "
                    f"Fills: {trade_count} | Merged Sets: {inv['complete_sets_merged']:.0f} | "
                    f"Realized PnL: +${realized_pnl:.2f} | Imbalance: {inv['net_imbalance']:+0.0f}"
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
    net_pnl = final_inv["net_pnl"]

    # Calculate returns and projections
    # Extrapolations:
    # 15 mins -> Hourly = * 4
    # Daily (24h) = Hourly * 24
    # Weekly (7d) = Daily * 7
    # Monthly (30d) = Daily * 30
    pnl_15m = realized_pnl
    return_15m_pct = (pnl_15m / starting_capital) * 100.0

    hourly_pnl = pnl_15m * (3600.0 / total_elapsed)
    daily_pnl = hourly_pnl * 24.0
    weekly_pnl = daily_pnl * 7.0
    monthly_pnl = daily_pnl * 30.0

    avg_pnl_per_trade = (realized_pnl / trade_count) if trade_count > 0 else 0.0
    avg_pnl_per_set = (realized_pnl / merged_sets) if merged_sets > 0 else 0.0

    report = {
        "starting_capital": starting_capital,
        "duration_minutes": round(total_elapsed / 60.0, 2),
        "total_trades_executed": trade_count,
        "complete_sets_merged": merged_sets,
        "realized_profit_15m": round(pnl_15m, 2),
        "return_15m_pct": round(return_15m_pct, 2),
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

    # Save benchmark json report
    with open("benchmark_15min_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=4)

    print("\n" + "=" * 80)
    print("📈 15-MINUTE BENCHMARK PERFORMANCE REPORT")
    print("=" * 80)
    print(f"Starting Capital          : ${starting_capital:.2f} USDC")
    print(f"Duration                  : {total_elapsed/60.0:.1f} Minutes")
    print(f"Total Trades Executed     : {trade_count}")
    print(f"Complete Sets Merged      : {merged_sets:.0f} (at $1.00 CTF redemption)")
    print(f"Active Residual Inventory : {final_inv['up_shares']} UP | {final_inv['down_shares']} DOWN")
    print(f"Net Realized Profit (15m) : +${realized_pnl:.2f} ({return_15m_pct:+.2f}%)")
    print(f"Avg Profit per Trade      : +${avg_pnl_per_trade:.4f}")
    print(f"Avg Profit per Complete Set: +${avg_pnl_per_set:.4f}")
    print("-" * 80)
    print("🔮 MATHEMATICAL PROJECTIONS (Based on 15m Run Rate):")
    print(f"   • Hourly Projected PnL : +${hourly_pnl:.2f} / hr")
    print(f"   • Daily Projected PnL  : +${daily_pnl:.2f} / day")
    print(f"   • Weekly Projected PnL : +${weekly_pnl:.2f} / week")
    print(f"   • Monthly Projected PnL: +${monthly_pnl:.2f} / month  (ROI: {report['projections']['monthly_roi_pct']}%)")
    print("=" * 80)
    print(f"Detailed logs saved to: benchmark_15min_trades.csv and benchmark_15min_report.json")


if __name__ == "__main__":
    asyncio.run(run_15min_benchmark(duration_seconds=900, starting_capital=300.0))
