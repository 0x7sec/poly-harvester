"""
Ultra-Low-Latency Binance WebSocket Feed client for real-time spot price and momentum tracking.
Supports direct Binance Futures stream, Binance Spot stream, Coinbase Pro fallback, and REST polling.
"""
import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable, Deque, Optional, Tuple
import websockets

logger = logging.getLogger(__name__)


class BinanceFeed:
    """
    Connects to high-frequency WebSocket streams (Binance Futures, Binance Spot, Coinbase Pro)
    for sub-50ms price discovery, price velocity (dPrice/dt), short-term volatility, and latency tracking.
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        lookback_seconds: int = 10,
        on_tick_callback: Optional[Callable[["BinanceFeed"], None]] = None,
    ):
        self.symbol = symbol.lower()
        self.lookback_seconds = lookback_seconds
        self.on_tick_callback = on_tick_callback

        # Real-time state
        self.current_price: float = 0.0
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_update_time: float = 0.0
        self.latency_ms: int = 24
        self._last_ping_send: float = 0.0

        # Ring buffer of historical ticks: (timestamp, price)
        self.price_history: Deque[Tuple[float, float]] = deque(maxlen=400)

        # Connection management
        self._running: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    def _fetch_rest_price_sync(self) -> float:
        """Synchronously polls public REST ticker endpoints as fallback."""
        endpoints = [
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={self.symbol.upper()}",
            "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            f"https://api.binance.com/api/v3/ticker/price?symbol={self.symbol.upper()}",
        ]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        for url in endpoints:
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if "price" in data:
                            return float(data["price"])
                        if "data" in data and "amount" in data["data"]:
                            return float(data["data"]["amount"])
            except Exception:
                continue
        return 0.0

    async def _bootstrap_price(self):
        """Fetches immediate price on boot so current_price is never $0.00."""
        try:
            p = await asyncio.to_thread(self._fetch_rest_price_sync)
            if p > 0:
                now = time.time()
                self.current_price = p
                self.best_bid = p
                self.best_ask = p
                self.last_update_time = now
                self.price_history.append((now, p))
                logger.info(f"⚡ [PRICE BOOTSTRAP] Initialized BTC reference price: ${p:,.2f}")
        except Exception as e:
            logger.debug(f"Bootstrap price fetch notice: {e}")

    async def start(self):
        """Starts the direct WebSocket listener with automatic failover and REST fallback."""
        self._running = True

        # Immediate bootstrap fetch
        await self._bootstrap_price()

        # Stream candidate endpoints ordered by reliability and speed
        ws_endpoints = [
            f"wss://fstream.binance.com/ws/{self.symbol}@bookTicker",
            f"wss://stream.binance.com:9443/ws/{self.symbol}@bookTicker",
            "wss://ws-feed.exchange.coinbase.com",
        ]

        endpoint_idx = 0
        while self._running:
            ws_url = ws_endpoints[endpoint_idx % len(ws_endpoints)]
            is_coinbase = "coinbase.com" in ws_url

            try:
                logger.info(f"Connecting to live price stream: {ws_url}...")
                async with websockets.connect(
                    ws_url,
                    ping_interval=15,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    logger.info(f"Connected to live reference feed ({ws_url}).")

                    if is_coinbase:
                        sub_msg = json.dumps({
                            "type": "subscribe",
                            "product_ids": ["BTC-USD"],
                            "channels": ["ticker"]
                        })
                        await ws.send(sub_msg)

                    ping_task = asyncio.create_task(self._periodic_ping(ws))

                    try:
                        async for message in ws:
                            if not self._running:
                                break
                            await self._handle_message(message, is_coinbase)
                    finally:
                        ping_task.cancel()

            except asyncio.CancelledError:
                logger.info("Binance feed cancelled.")
                break
            except Exception as e:
                logger.warning(f"Price stream notice ({ws_url}): {e}. Switching endpoint...")
                # Poll REST while switching
                p = await asyncio.to_thread(self._fetch_rest_price_sync)
                if p > 0:
                    now = time.time()
                    self.current_price = p
                    self.last_update_time = now
                    self.price_history.append((now, p))
                endpoint_idx += 1
                await asyncio.sleep(1.5)

    async def _periodic_ping(self, ws):
        """Measures WebSocket roundtrip ping latency with high-resolution timer."""
        while self._running and not ws.closed:
            try:
                t0 = time.perf_counter()
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=3.0)
                rtt = (time.perf_counter() - t0) * 1000.0
                if rtt > 0:
                    self.latency_ms = round(rtt, 1)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def _handle_message(self, raw_msg: str, is_coinbase: bool = False):
        """Processes incoming Binance or Coinbase ticker frames."""
        try:
            data = json.loads(raw_msg)
            now = time.time()

            if is_coinbase:
                if data.get("type") == "ticker" and "price" in data:
                    p = float(data["price"])
                    b = float(data.get("best_bid", p))
                    a = float(data.get("best_ask", p))
                    if p > 0:
                        self.current_price = p
                        self.best_bid = b
                        self.best_ask = a
                        self.last_update_time = now
                        self.price_history.append((now, p))
                        if self.on_tick_callback:
                            if asyncio.iscoroutinefunction(self.on_tick_callback):
                                await self.on_tick_callback(self)
                            else:
                                self.on_tick_callback(self)
                return

            # Binance bookTicker format: "b" = best bid, "a" = best ask
            if "b" in data and "a" in data:
                best_bid = float(data["b"])
                best_ask = float(data["a"])
                mid_price = (best_bid + best_ask) / 2.0

                if mid_price > 0:
                    self.current_price = mid_price
                    self.best_bid = best_bid
                    self.best_ask = best_ask
                    self.last_update_time = now
                    self.price_history.append((now, mid_price))

                    if self.on_tick_callback:
                        if asyncio.iscoroutinefunction(self.on_tick_callback):
                            await self.on_tick_callback(self)
                        else:
                            self.on_tick_callback(self)
        except Exception as e:
            logger.error(f"Error parsing ticker frame: {e}")

    def get_velocity(self) -> float:
        """Calculates spot price velocity (dPrice/dt in USD/sec) over the lookback window."""
        now = time.time()
        cutoff = now - self.lookback_seconds
        valid_ticks = [p for (t, p) in self.price_history if t >= cutoff]

        if len(valid_ticks) < 2:
            return 0.0

        oldest_price = valid_ticks[0]
        latest_price = valid_ticks[-1]
        dt = max(1.0, self.lookback_seconds)
        return (latest_price - oldest_price) / dt

    def get_percent_return(self) -> float:
        """Calculates percentage change over the lookback window."""
        now = time.time()
        cutoff = now - self.lookback_seconds
        valid_ticks = [p for (t, p) in self.price_history if t >= cutoff]

        if len(valid_ticks) < 2 or valid_ticks[0] == 0:
            return 0.0

        return (valid_ticks[-1] - valid_ticks[0]) / valid_ticks[0]

    async def stop(self):
        """Stops the feed."""
        self._running = False
        if self._ws:
            await self._ws.close()

