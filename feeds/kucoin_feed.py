"""
Async KuCoin WebSocket Feed client for real-time spot price and momentum tracking.
"""
import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple
import websockets

logger = logging.getLogger(__name__)


class KuCoinFeed:
    """
    Connects to KuCoin public WebSocket to stream real-time spot prices,
    best bid/ask, price velocity, and short-term volatility.
    """

    def __init__(
        self,
        symbol: str = "BTC-USDT",
        lookback_seconds: int = 10,
        on_tick_callback: Optional[Callable[["KuCoinFeed"], None]] = None,
    ):
        self.symbol = symbol
        self.lookback_seconds = lookback_seconds
        self.on_tick_callback = on_tick_callback

        # Real-time state
        self.current_price: float = 0.0
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_update_time: float = 0.0

        # Ring buffer of historical ticks: (timestamp, price)
        self.price_history: Deque[Tuple[float, float]] = deque(maxlen=300)

        # Connection management
        self._running: bool = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_bullet_token(self) -> Tuple[str, str, int]:
        """Fetches dynamic public bullet token and websocket endpoint from KuCoin REST API."""
        url = "https://api.kucoin.com/api/v1/bullet-public"
        
        def _fetch_sync():
            import urllib.request
            req = urllib.request.Request(url, data=b"", headers={"User-Agent": "PolymarketQuant/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))

        data = await asyncio.to_thread(_fetch_sync)
        if data.get("code") == "200000":
            token = data["data"]["token"]
            servers = data["data"]["instanceServers"]
            if servers:
                endpoint = servers[0]["endpoint"]
                ping_interval = servers[0].get("pingInterval", 18000)
                ws_url = f"{endpoint}?token={token}"
                return ws_url, token, ping_interval
        raise RuntimeError(f"Failed to fetch KuCoin bullet token: {data}")

    async def start(self):
        """Starts the KuCoin WebSocket listener loop with automatic reconnection."""
        self._running = True
        while self._running:
            try:
                ws_url, token, ping_interval_ms = await self._get_bullet_token()
                logger.info(f"Connecting to KuCoin WS for {self.symbol}...")

                async with websockets.connect(
                    ws_url,
                    ping_interval=None,  # We manage application-level ping as required by KuCoin
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws

                    # Subscribe to ticker topic
                    sub_msg = {
                        "id": int(time.time() * 1000),
                        "type": "subscribe",
                        "topic": f"/market/ticker:{self.symbol}",
                        "privateChannel": False,
                        "response": True,
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"Subscribed to KuCoin /market/ticker:{self.symbol}")

                    # Spawn periodic ping task
                    ping_task = asyncio.create_task(
                        self._ping_loop(ws, ping_interval_ms / 1000.0)
                    )

                    try:
                        async for message in ws:
                            if not self._running:
                                break
                            await self._handle_message(message)
                    finally:
                        ping_task.cancel()

            except asyncio.CancelledError:
                logger.info("KuCoin feed cancelled.")
                break
            except Exception as e:
                logger.warning(f"KuCoin WS error: {e}. Reconnecting in 3 seconds...")
                await asyncio.sleep(3.0)

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol, interval: float):
        """Sends required application-level ping frames to keep connection alive."""
        while self._running:
            try:
                await asyncio.sleep(interval * 0.8)
                ping_msg = {"id": int(time.time() * 1000), "type": "ping"}
                await ws.send(json.dumps(ping_msg))
            except Exception:
                break

    async def _handle_message(self, raw_msg: str):
        """Processes incoming KuCoin ticker message and updates price / velocity."""
        try:
            msg = json.loads(raw_msg)
            msg_type = msg.get("type")
            if msg_type == "message" and "data" in msg:
                data = msg["data"]
                price = float(data.get("price", 0.0))
                best_bid = float(data.get("bestBid", 0.0))
                best_ask = float(data.get("bestAsk", 0.0))
                now = time.time()

                if price > 0:
                    self.current_price = price
                    self.best_bid = best_bid
                    self.best_ask = best_ask
                    self.last_update_time = now
                    self.price_history.append((now, price))

                    if self.on_tick_callback:
                        if asyncio.iscoroutinefunction(self.on_tick_callback):
                            await self.on_tick_callback(self)
                        else:
                            self.on_tick_callback(self)
        except Exception as e:
            logger.error(f"Error handling KuCoin message: {e}")

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
        """Stops the KuCoin feed and closes connections."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
