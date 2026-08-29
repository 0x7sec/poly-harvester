"""
Async Polymarket CLOB Feed and Market Discovery client.
"""
import asyncio
import json
import logging
import time
from typing import Callable, Dict, List, Optional, Tuple
import websockets

logger = logging.getLogger(__name__)


class PolymarketFeed:
    """
    Discovers active BTC/ETH short-term Up/Down markets on Polymarket,
    and streams real-time order books (bids/asks/trades) via the CLOB WebSocket.
    """

    def __init__(
        self,
        token_id_up: str = "",
        token_id_down: str = "",
        auto_discover: bool = True,
        on_book_update_callback: Optional[Callable[["PolymarketFeed"], None]] = None,
    ):
        self.token_id_up = token_id_up
        self.token_id_down = token_id_down
        self.auto_discover = auto_discover
        self.on_book_update_callback = on_book_update_callback

        # Market Info
        self.market_title: str = "BTC Up or Down"
        self.market_end_time: Optional[float] = None
        self.market_slug: str = ""

        # Order Book State
        self.up_best_bid: float = 0.50
        self.up_best_ask: float = 0.51
        self.down_best_bid: float = 0.49
        self.down_best_ask: float = 0.50

        self.up_last_trade: float = 0.50
        self.down_last_trade: float = 0.50

        # Connection Management
        self._running: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def discover_active_crypto_market(self, search_term: str = "Bitcoin") -> bool:
        """
        Queries Polymarket Gamma API to find the most active short-term Bitcoin Up/Down market.
        """
        url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=20&order=volume24hr&ascending=false"

        def _fetch_gamma():
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "PolymarketQuant/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            markets = await asyncio.to_thread(_fetch_gamma)
            for m in markets:
                q = m.get("question", "").lower()
                slug = m.get("slug", "").lower()
                if ("bitcoin" in q or "btc" in q) and ("up" in q or "price" in q or "above" in q or "hit" in q or "dip" in q):
                    tokens = m.get("clobTokenIds")
                    if tokens and len(tokens) >= 2:
                        self.token_id_up = tokens[0]
                        self.token_id_down = tokens[1]
                        self.market_title = m.get("question", "BTC Market")
                        self.market_slug = slug
                        logger.info(f"Auto-discovered Polymarket: '{self.market_title}'")
                        logger.info(f"Token UP: {self.token_id_up} | Token DOWN: {self.token_id_down}")
                        return True
        except Exception as e:
            logger.warning(f"Error during auto-discovery: {e}. Using configured tokens or mock simulation.")

        return False

    async def fetch_clob_midpoints(self):
        """Fetches current REST order book snapshot from Polymarket CLOB."""
        if not self.token_id_up or not self.token_id_down:
            return

        def _fetch_book(token_id: str):
            import urllib.request
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "PolymarketQuant/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            book_up = await asyncio.to_thread(_fetch_book, self.token_id_up)
            bids = book_up.get("bids", [])
            asks = book_up.get("asks", [])
            if bids:
                self.up_best_bid = float(bids[0]["price"])
            if asks:
                self.up_best_ask = float(asks[0]["price"])

            book_down = await asyncio.to_thread(_fetch_book, self.token_id_down)
            bids = book_down.get("bids", [])
            asks = book_down.get("asks", [])
            if bids:
                self.down_best_bid = float(bids[0]["price"])
            if asks:
                self.down_best_ask = float(asks[0]["price"])

        except Exception as e:
            logger.debug(f"REST book fetch: {e}")

    async def start(self):
        """Starts the Polymarket CLOB WebSocket / polling stream."""
        self._running = True

        if self.auto_discover and (not self.token_id_up or not self.token_id_down):
            await self.discover_active_crypto_market()

        ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

        while self._running:
            try:
                # If we have valid tokens, connect to WebSocket
                if self.token_id_up and self.token_id_down:
                    logger.info(f"Connecting to Polymarket CLOB WS for {self.market_title}...")
                    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                        self._ws = ws

                        sub_msg = {
                            "type": "market",
                            "assets_ids": [self.token_id_up, self.token_id_down],
                        }
                        await ws.send(json.dumps(sub_msg))

                        # Fetch initial REST book snapshot
                        await self.fetch_clob_midpoints()

                        async for raw_msg in ws:
                            if not self._running:
                                break
                            await self._handle_ws_message(raw_msg)
                else:
                    # Fallback simulation updates
                    await self._simulate_live_ticks()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Polymarket WS error: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5.0)

    async def _handle_ws_message(self, raw_msg: str):
        """Processes Polymarket orderbook updates."""
        try:
            msg = json.loads(raw_msg)
            event_type = msg.get("event_type")
            asset_id = msg.get("asset_id")

            if event_type == "book":
                bids = msg.get("bids", [])
                asks = msg.get("asks", [])
                if asset_id == self.token_id_up:
                    if bids:
                        self.up_best_bid = float(bids[0]["price"])
                    if asks:
                        self.up_best_ask = float(asks[0]["price"])
                elif asset_id == self.token_id_down:
                    if bids:
                        self.down_best_bid = float(bids[0]["price"])
                    if asks:
                        self.down_best_ask = float(asks[0]["price"])

                if self.on_book_update_callback:
                    if asyncio.iscoroutinefunction(self.on_book_update_callback):
                        await self.on_book_update_callback(self)
                    else:
                        self.on_book_update_callback(self)

            elif event_type == "last_trade_price":
                price = float(msg.get("price", 0.0))
                if asset_id == self.token_id_up:
                    self.up_last_trade = price
                elif asset_id == self.token_id_down:
                    self.down_last_trade = price

        except Exception as e:
            logger.error(f"Error handling Polymarket message: {e}")

    async def _simulate_live_ticks(self):
        """Generates realistic market movements if offline or during dry-run sandbox testing."""
        await asyncio.sleep(1.0)
        # Small random walk around current prices
        import random
        drift = random.uniform(-0.01, 0.01)
        self.up_best_bid = max(0.05, min(0.95, round(self.up_best_bid + drift, 2)))
        self.up_best_ask = round(self.up_best_bid + 0.01, 2)
        self.down_best_bid = max(0.05, min(0.95, round(1.0 - self.up_best_ask, 2)))
        self.down_best_ask = round(self.down_best_bid + 0.01, 2)

        if self.on_book_update_callback:
            if asyncio.iscoroutinefunction(self.on_book_update_callback):
                await self.on_book_update_callback(self)
            else:
                self.on_book_update_callback(self)

    async def stop(self):
        """Stops the Polymarket feed."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
