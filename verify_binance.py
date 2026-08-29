import asyncio
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(os.path.join(os.path.dirname(__file__), "polymarket-quant"))

from feeds.binance_feed import BinanceFeed


async def verify_binance_stream():
    print("Testing Binance Direct Public WebSocket Feed...")
    feed = BinanceFeed(symbol="BTCUSDT")
    task = asyncio.create_task(feed.start())

    for _ in range(10):
        await asyncio.sleep(0.5)
        if feed.current_price > 0:
            print(
                f"✅ Connected to Binance Direct Feed!\n"
                f"   • Symbol: BTCUSDT\n"
                f"   • Live Price: ${feed.current_price:,.2f}\n"
                f"   • Best Bid: ${feed.best_bid:,.2f} | Best Ask: ${feed.best_ask:,.2f}\n"
                f"   • Velocity: {feed.get_velocity():+.2f} $/s"
            )
            break

    task.cancel()
    await feed.stop()
    print("Binance verification successful.")


if __name__ == "__main__":
    asyncio.run(verify_binance_stream())
