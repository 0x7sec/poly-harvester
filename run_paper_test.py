"""
Automated Paper Trading Test Runner.
Connects to live KuCoin WebSockets and Polymarket CLOB, executes paper trading cycles,
and displays the resulting metrics and trade logs.
"""
import asyncio
import os
import sys
import time

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__)))

from config import BotConfig
from main import PolymarketQuantEngine


async def run_timed_paper_test(duration_seconds: int = 35):
    print("=" * 75)
    print(f"[*] LAUNCHING {duration_seconds}-SECOND LIVE PAPER TRADING TEST")
    print("    Reference: KuCoin Spot WSS (BTC-USDT) -> Polymarket CLOB Simulator")
    print("    Mode: Zero-Risk Shadow Paper Trading (Zero Capital Required)")
    print("=" * 75)

    config = BotConfig(
        dry_run=True,
        target_edge_per_share=0.015,
        max_combined_cost=0.985,
        order_size_shares=50.0,
        trade_log_file="trades_simulated.csv",
    )

    engine = PolymarketQuantEngine(config)

    # Start feeds and quoting
    feed_kc = asyncio.create_task(engine.kucoin_feed.start())
    feed_pm = asyncio.create_task(engine.polymarket_feed.start())

    start_time = time.time()
    tick_count = 0

    try:
        while time.time() - start_time < duration_seconds:
            await asyncio.sleep(2.0)
            elapsed = int(time.time() - start_time)

            kc_price = engine.kucoin_feed.current_price
            velocity = engine.kucoin_feed.get_velocity()
            q_up = engine.fair_prob.get("q_up", 0.50)
            quotes = engine.current_quotes
            inv = engine.inventory.get_summary()

            if kc_price > 0:
                tick_count += 1
                q_up_val = quotes.get("quote_up", 0.0)
                q_dn_val = quotes.get("quote_down", 0.0)
                sum_cost = quotes.get("projected_cost", 0.0)
                edge_pct = quotes.get("projected_edge", 0.0) * 100.0

                print(
                    f"[{elapsed:02d}s/{duration_seconds}s] "
                    f"KuCoin: ${kc_price:,.2f} (Vel: {velocity:+.2f}$/s) | "
                    f"Fair P(UP): {q_up:.3f} | "
                    f"Quotes: UP=${q_up_val:.2f} DN=${q_dn_val:.2f} (Sum: ${sum_cost:.3f} | Edge: {edge_pct:.1f}%) | "
                    f"Merged Sets: {inv['complete_sets_merged']:.0f} | Realized PnL: +${inv['realized_arb_pnl']:.2f}"
                )

    finally:
        feed_kc.cancel()
        feed_pm.cancel()
        await engine.kucoin_feed.stop()
        await engine.polymarket_feed.stop()

    print("\n" + "=" * 75)
    print("[-] PAPER TRADING RUN COMPLETED")
    print("=" * 75)
    final_inv = engine.inventory.get_summary()
    print(f"Total Ticks Processed    : {tick_count}")
    print(f"Active UP Inventory      : {final_inv['up_shares']} shares (@ avg ${final_inv['up_avg_cost']:.2f})")
    print(f"Active DOWN Inventory    : {final_inv['down_shares']} shares (@ avg ${final_inv['down_avg_cost']:.2f})")
    print(f"Net Inventory Imbalance  : {final_inv['net_imbalance']:+0.1f}")
    print(f"Complete Sets Merged     : {final_inv['complete_sets_merged']:.0f} ($1.00 CTF redemptions)")
    print(f"Locked Arbitrage PnL     : +${final_inv['realized_arb_pnl']:.2f}")
    print(f"Log File Generated       : {config.trade_log_file}")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_timed_paper_test(duration_seconds=35))
