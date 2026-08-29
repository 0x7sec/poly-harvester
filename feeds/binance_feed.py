"""
Ultra-Low-Latency Binance WebSocket Feed client for real-time spot price and momentum tracking.
Public, zero-authentication, direct 50ms bookTicker stream with ping latency tracking.
"""
import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple
import websockets

logger = logging.getLogger(__name__)


class BinanceFeed:
    """
    Connects to Binance's direct public WebSocket stream (@bookTicker) for sub-50ms price
    discovery, price velocity (dPrice/dt), short-term volatility, and latency tracking.
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

    async def start(self):
        """Starts the direct Binance WebSocket listener with automatic reconnection."""
        self._running = True
        ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@bookTicker"

        while self._running:
            try:
                logger.info(f"Connecting to Binance direct stream: {ws_url}...")
                async with websockets.connect(
                    ws_url,
                    ping_interval=15,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    logger.info(f"Connected to Binance {self.symbol.upper()} feed.")

                    # Start periodic ping task
                    ping_task = asyncio.create_task(self._periodic_ping(ws))

                    try:
                        async for message in ws:
                            if not self._running:
                                break
                            await self._handle_message(message)
                    finally:
                        ping_task.cancel()

            except asyncio.CancelledError:
                logger.info("Binance feed cancelled.")
                break
            except Exception as e:
                logger.warning(f"Binance WS error: {e}. Reconnecting in 2 seconds...")
                await asyncio.sleep(2.0)

    async def _periodic_ping(self, ws):
        """Measures WebSocket roundtrip ping latency to Binance."""
        while self._running and not ws.closed:
            try:
                t0 = time.time()
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=4.0)
                rtt = int((time.time() - t0) * 1000)
                if rtt > 0:
                    self.latency_ms = rtt
            except Exception:
                pass
            await asyncio.sleep(3.0)

    async def _handle_message(self, raw_msg: str):
        """Processes incoming Binance bookTicker frame."""
        try:
            data = json.loads(raw_msg)
            # Binance bookTicker keys: "b" = best bid price, "a" = best ask price
            if "b" in data and "a" in data:
                best_bid = float(data["b"])
                best_ask = float(data["a"])
                mid_price = (best_bid + best_ask) / 2.0
                now = time.time()

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
            logger.error(f"Error parsing Binance message: {e}")

    def get_velocity(self) -> float:
        """
        Calculates spot price velocity (dPrice/dt in USD/sec) over the lookback window.
        Positive = upward momentum, Negative = downward momentum.
        """
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
        """Stops the Binance feed."""
        self._running = False
        if self._ws:
            await self._ws.close()
