import asyncio
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "polymarket-quant"))

from feeds.kucoin_feed import KuCoinFeed
from feeds.polymarket_feed import PolymarketFeed


async def test_live_feeds():
    print("Testing KuCoin WebSocket & Polymarket Discovery...")
    
    # 1. Test KuCoin Feed
    kc = KuCoinFeed(symbol="BTC-USDT")
    task_kc = asyncio.create_task(kc.start())

    # 2. Test Polymarket Feed
    pm = PolymarketFeed(auto_discover=True)
    discovered = await pm.discover_active_crypto_market()
    print(f"Polymarket Auto-Discovery Success: {discovered} | Market: {pm.market_title}")

    # Wait for a couple ticks from KuCoin
    for _ in range(10):
        await asyncio.sleep(0.5)
        if kc.current_price > 0:
            print(f"KuCoin Live Price Received: ${kc.current_price:,.2f} | Velocity: {kc.get_velocity():+.2f} $/s")
            break

    # Stop feeds
    task_kc.cancel()
    await kc.stop()
    await pm.stop()
    print("Live feed connection test completed successfully!")


if __name__ == "__main__":
    asyncio.run(test_live_feeds())
