"""
Async Polymarket CLOB Feed and Market Discovery client with latency tracking.
"""
import asyncio
import collections
from collections import deque
import json
import logging
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
import websockets

logger = logging.getLogger(__name__)


class PolymarketFeed:
    """
    Discovers active BTC/ETH short-term Up/Down markets on Polymarket,
    and streams real-time order books (bids/asks/trades) with full L2 depth and ping tracking.
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
        self.condition_id: str = ""
        self.latency_ms: int = 38

        # Order Book Best Prices
        self.up_best_bid: float = 0.50
        self.up_best_ask: float = 0.51
        self.down_best_bid: float = 0.49
        self.down_best_ask: float = 0.50

        # L2 Depth: List of {"price": float, "size": float}
        self.up_bids: List[Dict[str, float]] = [{"price": 0.50, "size": 100.0}]
        self.up_asks: List[Dict[str, float]] = [{"price": 0.51, "size": 100.0}]
        self.down_bids: List[Dict[str, float]] = [{"price": 0.49, "size": 100.0}]
        self.down_asks: List[Dict[str, float]] = [{"price": 0.50, "size": 100.0}]

        self.up_last_trade: float = 0.50
        self.down_last_trade: float = 0.50

        # Real Trade Prints Buffer: {"side": "UP"|"DOWN", "price": float, "size": float, "time": float}
        self.trade_events: Deque[Dict[str, Any]] = deque(maxlen=200)
        self._unprocessed_trades: List[Dict[str, Any]] = []

        # Connection Management
        self._running: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    def get_bid_depth_ahead(self, side: str, price: float) -> float:
        """
        Calculates total volume of existing bids sitting ahead of a new order at `price`.
        Orders with bid price > `price`, or equal to `price` (FIFO priority), sit ahead.
        """
        book_bids = self.up_bids if side.upper() == "UP" else self.down_bids
        depth = 0.0
        for b in book_bids:
            p = b.get("price", 0.0)
            sz = b.get("size", 0.0)
            if p >= price:
                depth += sz
        return max(0.0, depth)

    def get_ask_depth_at_or_below(self, side: str, price: float) -> float:
        """
        Calculates total ask volume available at or below `price` (for immediate crossing).
        """
        book_asks = self.up_asks if side.upper() == "UP" else self.down_asks
        depth = 0.0
        for a in book_asks:
            p = a.get("price", 0.0)
            sz = a.get("size", 0.0)
            if p <= price:
                depth += sz
        return max(0.0, depth)

    def pop_new_trades(self) -> List[Dict[str, Any]]:
        """Returns and clears all unconsumed real market trade prints."""
        trades = list(self._unprocessed_trades)
        self._unprocessed_trades.clear()
        return trades

    def record_simulated_trade(self, side: str, price: float, size: float = 20.0):
        """Helper to record a trade print."""
        evt = {
            "side": side.upper(),
            "price": float(price),
            "size": float(size),
            "timestamp": time.time(),
        }
        self.trade_events.append(evt)
        self._unprocessed_trades.append(evt)

    async def discover_active_crypto_market(self, search_term: str = "Bitcoin") -> bool:
        """
        Queries Polymarket Gamma API to find the most active short-term Bitcoin Up/Down market
        across 15-minute, 1-hour, or 5-minute contract cycles.
        """
        url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=40&order=volume24hr&ascending=false"

        def _fetch_gamma():
            import urllib.request
            t0 = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "PolymarketQuant/1.2"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.latency_ms = max(10, int((time.time() - t0) * 1000))
                return data

        try:
            markets = await asyncio.to_thread(_fetch_gamma)
            # Prioritize Bitcoin specific short term markets first
            candidates = []
            for m in markets:
                q = m.get("question", "").lower()
                slug = m.get("slug", "").lower()
                is_btc = "bitcoin" in q or "btc" in q or "bitcoin" in slug or "btc" in slug
                is_crypto = is_btc or any(k in q or k in slug for k in ["eth", "ethereum", "solana"])
                is_up_down = any(k in q or k in slug for k in ["up or down", "up/down", "price", "above", "15m", "1h", "5m", "hit", "dip"])

                if is_crypto and is_up_down:
                    tokens = m.get("clobTokenIds")
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if tokens and len(tokens) >= 2:
                        candidates.append({
                            "market": m,
                            "is_btc": is_btc,
                            "tokens": tokens,
                            "vol": float(m.get("volume24hr") or 0.0)
                        })

            # Sort candidate by BTC preference then 24h volume
            candidates.sort(key=lambda x: (x["is_btc"], x["vol"]), reverse=True)

            if candidates:
                best = candidates[0]
                m = best["market"]
                tokens = best["tokens"]
                self.token_id_up = str(tokens[0])
                self.token_id_down = str(tokens[1])
                self.market_title = m.get("question", "BTC Up/Down Market")
                self.market_slug = m.get("slug", "")
                self.condition_id = str(m.get("conditionId", ""))
                logger.info(f"Auto-discovered Polymarket ({self.market_slug}): '{self.market_title}'")
                logger.info(f"Condition ID: {self.condition_id} | Token UP: {self.token_id_up} | Token DOWN: {self.token_id_down}")
                return True
        except Exception as e:
            logger.warning(f"Error during auto-discovery: {e}. Using configured tokens or mock simulation.")

        return False

    async def fetch_clob_midpoints(self):
        """Fetches current REST order book snapshot with full L2 depth from Polymarket CLOB."""
        if not self.token_id_up or not self.token_id_down:
            return

        def _fetch_book(token_id: str):
            import urllib.request
            t0 = time.time()
            url = f"https://clob.polymarket.com/book?token_id={token_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "PolymarketQuant/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.latency_ms = max(10, int((time.time() - t0) * 1000))
                return data

        try:
            book_up = await asyncio.to_thread(_fetch_book, self.token_id_up)
            bids = book_up.get("bids", [])
            asks = book_up.get("asks", [])
            self.up_bids = [{"price": float(b["price"]), "size": float(b.get("size", 100.0))} for b in bids if "price" in b]
            self.up_asks = [{"price": float(a["price"]), "size": float(a.get("size", 100.0))} for a in asks if "price" in a]
            if self.up_bids:
                self.up_best_bid = self.up_bids[0]["price"]
            if self.up_asks:
                self.up_best_ask = self.up_asks[0]["price"]

            book_down = await asyncio.to_thread(_fetch_book, self.token_id_down)
            bids = book_down.get("bids", [])
            asks = book_down.get("asks", [])
            self.down_bids = [{"price": float(b["price"]), "size": float(b.get("size", 100.0))} for b in bids if "price" in b]
            self.down_asks = [{"price": float(a["price"]), "size": float(a.get("size", 100.0))} for a in asks if "price" in a]
            if self.down_bids:
                self.down_best_bid = self.down_bids[0]["price"]
            if self.down_asks:
                self.down_best_ask = self.down_asks[0]["price"]

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
                        ping_task = asyncio.create_task(self._periodic_ping(ws))

                        try:
                            async for raw_msg in ws:
                                if not self._running:
                                    break
                                await self._handle_ws_message(raw_msg)
                        finally:
                            ping_task.cancel()
                else:
                    # Fallback simulation updates
                    await self._simulate_live_ticks()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Polymarket WS error: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5.0)

    async def _periodic_ping(self, ws):
        """Measures WebSocket roundtrip ping latency to Polymarket."""
        while self._running and not ws.closed:
            try:
                t0 = time.time()
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=5.0)
                rtt = int((time.time() - t0) * 1000)
                if rtt > 0:
                    self.latency_ms = rtt
            except Exception:
                pass
            await asyncio.sleep(4.0)

    async def _handle_ws_message(self, raw_msg: str):
        """Processes Polymarket orderbook updates."""
        try:
            msg = json.loads(raw_msg)
            event_type = msg.get("event_type")
            asset_id = msg.get("asset_id")

            if event_type == "book":
                bids = msg.get("bids", [])
                asks = msg.get("asks", [])
                parsed_bids = [{"price": float(b["price"]), "size": float(b.get("size", 100.0))} for b in bids if "price" in b]
                parsed_asks = [{"price": float(a["price"]), "size": float(a.get("size", 100.0))} for a in asks if "price" in a]

                if asset_id == self.token_id_up:
                    if parsed_bids:
                        self.up_bids = parsed_bids
                        self.up_best_bid = parsed_bids[0]["price"]
                    if parsed_asks:
                        self.up_asks = parsed_asks
                        self.up_best_ask = parsed_asks[0]["price"]
                elif asset_id == self.token_id_down:
                    if parsed_bids:
                        self.down_bids = parsed_bids
                        self.down_best_bid = parsed_bids[0]["price"]
                    if parsed_asks:
                        self.down_asks = parsed_asks
                        self.down_best_ask = parsed_asks[0]["price"]

                if self.on_book_update_callback:
                    if asyncio.iscoroutinefunction(self.on_book_update_callback):
                        await self.on_book_update_callback(self)
                    else:
                        self.on_book_update_callback(self)

            elif event_type == "last_trade_price":
                price = float(msg.get("price", 0.0))
                size = float(msg.get("size", 20.0))
                side = "UP" if asset_id == self.token_id_up else "DOWN"
                if asset_id == self.token_id_up:
                    self.up_last_trade = price
                elif asset_id == self.token_id_down:
                    self.down_last_trade = price

                trade_evt = {
                    "side": side,
                    "price": price,
                    "size": size,
                    "timestamp": time.time(),
                }
                self.trade_events.append(trade_evt)
                self._unprocessed_trades.append(trade_evt)

                if self.on_book_update_callback:
                    if asyncio.iscoroutinefunction(self.on_book_update_callback):
                        await self.on_book_update_callback(self)
                    else:
                        self.on_book_update_callback(self)

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

        self.up_bids = [{"price": self.up_best_bid, "size": 150.0}]
        self.up_asks = [{"price": self.up_best_ask, "size": 150.0}]
        self.down_bids = [{"price": self.down_best_bid, "size": 150.0}]
        self.down_asks = [{"price": self.down_best_ask, "size": 150.0}]

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
