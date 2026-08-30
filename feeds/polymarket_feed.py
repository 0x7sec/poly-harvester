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
        target_market_slug: str = "",
        target_timeframe: str = "5M",
        on_book_update_callback: Optional[Callable[["PolymarketFeed"], None]] = None,
        on_rollover_callback: Optional[Callable] = None,
    ):
        self.token_id_up = token_id_up
        self.token_id_down = token_id_down
        self.auto_discover = auto_discover
        self.target_market_slug = target_market_slug
        self.target_timeframe = target_timeframe.upper().strip()
        self.on_book_update_callback = on_book_update_callback
        self.on_rollover_callback = on_rollover_callback

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

    async def switch_timeframe(self, timeframe: str) -> bool:
        """Dynamically switches between 5M, 15M, 1H, 24H contract timeframes."""
        self.target_timeframe = timeframe.upper().strip()
        logger.info(f"🔄 Switching target timeframe to: {self.target_timeframe}")
        success = await self.discover_active_crypto_market()
        if success:
            await self.fetch_clob_midpoints()
            if self._ws and not self._ws.closed:
                try:
                    sub_msg = {
                        "type": "market",
                        "assets_ids": [self.token_id_up, self.token_id_down],
                    }
                    await self._ws.send(json.dumps(sub_msg))
                except Exception as exc:
                    logger.debug(f"WS resubscribe error on timeframe switch: {exc}")

            if self.on_book_update_callback:
                res = self.on_book_update_callback(self)
                if asyncio.iscoroutine(res):
                    await res
        return success

    def get_order_book_obj(self, side: str):
        """Returns a pm_trader OrderBook object for exact level-by-level walking."""
        from pm_trader.models import OrderBook, OrderBookLevel
        if side.upper() == "UP":
            bids = [OrderBookLevel(price=b["price"], size=b.get("size", 100.0)) for b in self.up_bids]
            asks = [OrderBookLevel(price=a["price"], size=a.get("size", 100.0)) for a in self.up_asks]
        else:
            bids = [OrderBookLevel(price=b["price"], size=b.get("size", 100.0)) for b in self.down_bids]
            asks = [OrderBookLevel(price=a["price"], size=a.get("size", 100.0)) for a in self.down_asks]
        return OrderBook(bids=bids, asks=asks)

    def get_bid_depth_ahead(self, side: str, price: float) -> float:
        """Calculates the cumulative maker volume ahead in queue at or better than quote price."""
        depth = 0.0
        bids = self.up_bids if side.upper() == "UP" else self.down_bids
        for b in bids:
            if b["price"] >= price:
                depth += b.get("size", 0.0)
            else:
                break
        return depth

    def get_ask_depth_at_or_below(self, side: str, price: float) -> float:
        """Calculates total ask volume available at or below price (for immediate crossing)."""
        book_asks = self.up_asks if side.upper() == "UP" else self.down_asks
        depth = 0.0
        for a in book_asks:
            p = a.get("price", 0.0)
            sz = a.get("size", 0.0)
            if p <= price:
                depth += sz
        return max(0.0, depth)

    def get_unprocessed_trades(self) -> List[Dict[str, Any]]:
        """Returns new trade prints since last check and clears internal buffer."""
        trades = list(self._unprocessed_trades)
        self._unprocessed_trades.clear()
        return trades

    def pop_new_trades(self) -> List[Dict[str, Any]]:
        """Alias for get_unprocessed_trades."""
        return self.get_unprocessed_trades()

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

    async def discover_active_crypto_market(self, search_slug: Optional[str] = None) -> bool:
        """
        Discovers the live active short-term Bitcoin Up/Down market based on target_timeframe
        (5-minute, 15-minute, 1-hour, or 24-hour daily slots) or a specific slug from Polymarket Gamma API.
        """
        def _fetch_url(url: str):
            import urllib.request
            t0 = time.time()
            req = urllib.request.Request(url, headers={"User-Agent": "PolymarketQuant/1.2"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.latency_ms = max(10, int((time.time() - t0) * 1000))
                return data

        candidate_slugs = []

        # 1. Check explicit slug if configured
        target = search_slug or self.target_market_slug
        if target and target.strip() and target.strip() not in ("btc-15m-up-down", "btc-5m-up-down"):
            candidate_slugs.append(target.strip())

        # 2. Compute rolling 5m and 15m deterministic time slots
        now_ts = int(time.time())
        current_5m = (now_ts // 300) * 300
        next_5m = current_5m + 300
        current_15m = (now_ts // 900) * 900
        next_15m = current_15m + 900
        current_1h = (now_ts // 3600) * 3600
        next_1h = current_1h + 3600

        # Prioritize according to target_timeframe
        tf = getattr(self, "target_timeframe", "5M")
        if tf == "15M":
            candidate_slugs.extend([
                f"btc-updown-15m-{current_15m}",
                f"btc-updown-15m-{next_15m}",
                f"btc-updown-5m-{current_5m}",
                f"btc-updown-5m-{next_5m}",
            ])
        elif tf == "1H":
            candidate_slugs.extend([
                f"btc-updown-1h-{current_1h}",
                f"btc-updown-1h-{next_1h}",
                f"btc-updown-15m-{current_15m}",
                f"btc-updown-5m-{current_5m}",
            ])
        elif tf == "24H":
            pass # Skip straight to daily / highest volume markets
        else: # Default 5M
            candidate_slugs.extend([
                f"btc-updown-5m-{current_5m}",
                f"btc-updown-5m-{next_5m}",
                f"btc-updown-15m-{current_15m}",
                f"btc-updown-15m-{next_15m}",
            ])

        # Try discrete short-term slug endpoints first
        for slug in candidate_slugs:
            try:
                # Extract slot timestamp and duration
                slot_ts = int(slug.split("-")[-1])
                dur = 300 if "5m" in slug else (900 if "15m" in slug else 3600)
                market_end = float(slot_ts + dur)
                secs_remaining = market_end - time.time()

                # Rule 1: Skip if contract has expired or has less than 40s of life remaining
                if secs_remaining < 40:
                    logger.debug(f"Skipping candidate {slug}: only {int(secs_remaining)}s remaining (expired/closing).")
                    continue

                events = await asyncio.to_thread(_fetch_url, f"https://gamma-api.polymarket.com/events?slug={slug}")
                if events and isinstance(events, list) and len(events) > 0:
                    e = events[0]
                    for m in e.get("markets", []):
                        if m.get("active", True) and not m.get("closed", False):
                            # Rule 2: Check outcomePrices to ensure market is not already settled/resolved
                            outcome_prices = m.get("outcomePrices")
                            if isinstance(outcome_prices, str):
                                try:
                                    outcome_prices = json.loads(outcome_prices)
                                except Exception:
                                    outcome_prices = []
                            
                            if outcome_prices and len(outcome_prices) >= 2:
                                try:
                                    p0, p1 = float(outcome_prices[0]), float(outcome_prices[1])
                                    if p0 >= 0.88 or p0 <= 0.12 or p1 >= 0.88 or p1 <= 0.12:
                                        logger.info(f"Skipping candidate {slug} ('{e.get('title')}'): prices {outcome_prices} indicates outcome already determined.")
                                        continue
                                except Exception:
                                    pass

                            tokens = m.get("clobTokenIds")
                            if isinstance(tokens, str):
                                tokens = json.loads(tokens)
                            if tokens and len(tokens) >= 2:
                                old_slug = self.market_slug
                                old_title = self.market_title

                                self.token_id_up = str(tokens[0])
                                self.token_id_down = str(tokens[1])
                                self.market_title = e.get("title") or m.get("question", "Bitcoin Up or Down 5m")
                                self.market_slug = e.get("slug") or m.get("slug", slug)
                                self.condition_id = str(m.get("conditionId", ""))
                                self.market_end_time = market_end

                                logger.info(f"🎯 Target Short-Term Contract ({self.market_slug}): '{self.market_title}' | Expiry: in {int(self.market_end_time - time.time())}s")
                                logger.info(f"Condition ID: {self.condition_id} | Token UP: {self.token_id_up} | Token DOWN: {self.token_id_down}")

                                # Trigger contract round rollover and settlement if switching to a new contract
                                if old_slug and old_slug != self.market_slug and self.on_rollover_callback:
                                    winning_side = "UP" if (self.up_best_bid >= 0.50 and self.up_best_bid >= self.down_best_bid) else ("DOWN" if (self.down_best_bid >= 0.50 and self.down_best_bid > self.up_best_bid) else None)
                                    try:
                                        res = self.on_rollover_callback(old_slug, old_title, self.market_slug, self.market_title, winning_side)
                                        if asyncio.iscoroutine(res):
                                            asyncio.create_task(res)
                                    except Exception as err:
                                        logger.error(f"Error in rollover callback: {err}")

                                return True
            except Exception as exc:
                logger.debug(f"Slug check error for {slug}: {exc}")

        # 3. Fallback to top volume crypto markets if 5m/15m are unavailable
        url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=40&order=volume24hr&ascending=false"
        try:
            markets = await asyncio.to_thread(_fetch_url, url)
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
            logger.warning(f"Error during auto-discovery fallback: {e}.")

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
            parsed_bids = [{"price": float(b["price"]), "size": float(b.get("size", 100.0))} for b in bids if "price" in b]
            parsed_asks = [{"price": float(a["price"]), "size": float(a.get("size", 100.0))} for a in asks if "price" in a]
            parsed_bids.sort(key=lambda x: x["price"], reverse=True)
            parsed_asks.sort(key=lambda x: x["price"], reverse=False)

            self.up_bids = parsed_bids
            self.up_asks = parsed_asks
            if self.up_bids:
                self.up_best_bid = self.up_bids[0]["price"]
            if self.up_asks:
                self.up_best_ask = self.up_asks[0]["price"]

            book_down = await asyncio.to_thread(_fetch_book, self.token_id_down)
            bids = book_down.get("bids", [])
            asks = book_down.get("asks", [])
            parsed_bids = [{"price": float(b["price"]), "size": float(b.get("size", 100.0))} for b in bids if "price" in b]
            parsed_asks = [{"price": float(a["price"]), "size": float(a.get("size", 100.0))} for a in asks if "price" in a]
            parsed_bids.sort(key=lambda x: x["price"], reverse=True)
            parsed_asks.sort(key=lambda x: x["price"], reverse=False)

            self.down_bids = parsed_bids
            self.down_asks = parsed_asks
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
        """Measures WebSocket roundtrip ping latency and triggers automatic contract rollover upon expiry."""
        while self._running and not ws.closed:
            try:
                t0 = time.perf_counter()
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=4.0)
                rtt = (time.perf_counter() - t0) * 1000.0
                if rtt > 0:
                    self.latency_ms = round(rtt, 1)

                # Check contract expiry auto-rollover for rolling 5M/15M slots
                if self.auto_discover and self.market_end_time and time.time() >= self.market_end_time:
                    logger.info(f"⏳ Contract window ended ('{self.market_title}'). Auto-rolling over to fresh {self.target_timeframe} contract...")
                    await self.switch_timeframe(self.target_timeframe)
                    break
            except Exception:
                pass
            await asyncio.sleep(1.0)

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
                parsed_bids.sort(key=lambda x: x["price"], reverse=True)
                parsed_asks.sort(key=lambda x: x["price"], reverse=False)

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
