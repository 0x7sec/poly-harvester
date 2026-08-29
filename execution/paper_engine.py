"""
Zero-Risk Paper Trading & Order Matching Simulator.
"""
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from models.inventory import InventoryManager

logger = logging.getLogger(__name__)


@dataclass
class VirtualOrder:
    order_id: str
    token_side: str  # "UP" or "DOWN"
    price: float
    shares: float
    created_at: float
    is_active: bool = True


class PaperTradingEngine:
    """
    Simulates real-world limit order matching against live Polymarket orderbook data.
    Ensures zero financial risk while testing strategy edge and fill mechanics.
    """

    def __init__(self, inventory: InventoryManager, order_size_shares: float = 20.0, db=None):
        self.inventory = inventory
        self.order_size_shares = order_size_shares
        self.db = db

        # Active virtual orders
        self.active_order_up: Optional[VirtualOrder] = None
        self.active_order_down: Optional[VirtualOrder] = None

        # Fill history (hydrated from DB if available)
        self.fill_history: List[dict] = []
        if self.db:
            try:
                self.fill_history = self.db.get_recent_trades(limit=100)
                # Reverse to keep chronological order
                self.fill_history.reverse()
            except Exception:
                pass
        self._order_counter: int = len(self.fill_history)

    def update_quotes(
        self,
        quote_up: float,
        quote_down: float,
        allow_up: bool,
        allow_down: bool,
    ):
        """Updates or replaces current active virtual limit orders."""
        now = time.time()

        # Update UP order
        if allow_up:
            if not self.active_order_up or self.active_order_up.price != quote_up:
                self._order_counter += 1
                self.active_order_up = VirtualOrder(
                    order_id=f"SIM-UP-{self._order_counter}",
                    token_side="UP",
                    price=quote_up,
                    shares=self.order_size_shares,
                    created_at=now,
                )
        else:
            self.active_order_up = None

        # Update DOWN order
        if allow_down:
            if not self.active_order_down or self.active_order_down.price != quote_down:
                self._order_counter += 1
                self.active_order_down = VirtualOrder(
                    order_id=f"SIM-DOWN-{self._order_counter}",
                    token_side="DOWN",
                    price=quote_down,
                    shares=self.order_size_shares,
                    created_at=now,
                )
        else:
            self.active_order_down = None

    def check_fills(
        self,
        up_market_ask: float,
        up_last_trade: float,
        down_market_ask: float,
        down_last_trade: float,
    ) -> List[dict]:
        """
        Checks if the live market price has crossed our virtual limit orders.
        A limit bid fills if the market ask or last trade price is <= our limit bid.
        """
        filled_events = []
        now = time.time()

        # Check UP limit bid fill
        if self.active_order_up and self.active_order_up.is_active:
            # If someone sold at or below our bid price
            if (up_market_ask > 0 and up_market_ask <= self.active_order_up.price) or (
                up_last_trade > 0 and up_last_trade <= self.active_order_up.price
            ):
                fill_price = self.active_order_up.price
                fill_shares = self.active_order_up.shares
                fee = 0.0  # Limit orders on Polymarket typically incur 0% maker fees

                # Record fill in inventory
                self.inventory.on_fill("UP", fill_price, fill_shares, fee)

                event = {
                    "timestamp": now,
                    "order_id": self.active_order_up.order_id,
                    "side": "UP",
                    "price": fill_price,
                    "shares": fill_shares,
                    "cost": round(fill_price * fill_shares, 2),
                    "fee": fee,
                }
                if self.db:
                    self.db.log_trade(event, self.inventory.up.shares, self.inventory.down.shares, "PAPER")

                filled_events.append(event)
                self.fill_history.append(event)
                self.active_order_up.is_active = False
                self.active_order_up = None

        # Check DOWN limit bid fill
        if self.active_order_down and self.active_order_down.is_active:
            if (down_market_ask > 0 and down_market_ask <= self.active_order_down.price) or (
                down_last_trade > 0 and down_last_trade <= self.active_order_down.price
            ):
                fill_price = self.active_order_down.price
                fill_shares = self.active_order_down.shares
                fee = 0.0

                self.inventory.on_fill("DOWN", fill_price, fill_shares, fee)

                event = {
                    "timestamp": now,
                    "order_id": self.active_order_down.order_id,
                    "side": "DOWN",
                    "price": fill_price,
                    "shares": fill_shares,
                    "cost": round(fill_price * fill_shares, 2),
                    "fee": fee,
                }
                if self.db:
                    self.db.log_trade(event, self.inventory.up.shares, self.inventory.down.shares, "PAPER")

                filled_events.append(event)
                self.fill_history.append(event)
                self.active_order_down.is_active = False
                self.active_order_down = None

        return filled_events
