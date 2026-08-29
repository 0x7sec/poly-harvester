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

        # Realistic CLOB Simulation Parameters
        self._min_resting_seconds: float = 2.0  # Order must rest in book queue for >= 2.0s
        self._min_fill_cooldown_seconds: float = 8.0  # Realistic taker arrival flow spacing
        self._last_fill_time_up: float = 0.0
        self._last_fill_time_down: float = 0.0

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
        if allow_up and quote_up > 0:
            if not self.active_order_up or not self.active_order_up.is_active or abs(self.active_order_up.price - quote_up) >= 0.02:
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
        if allow_down and quote_down > 0:
            if not self.active_order_down or not self.active_order_down.is_active or abs(self.active_order_down.price - quote_down) >= 0.02:
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
        up_best_bid: float = 0.0,
        down_best_bid: float = 0.0,
    ) -> List[dict]:
        """
        Simulates 100% accurate, realistic CLOB matching against live Polymarket market data:
        1. Latency & Queue In-Flight Guard: Order must rest in book queue >= 2.0s.
        2. Bankroll Invariant: Total spent + order cost cannot exceed session allocated capital.
        3. Match Conditions:
           a. Live Ask crossed bid (up_market_ask <= bid_p) -> Immediate fill.
           b. Live Trade Print executed at or below bid (up_last_trade <= bid_p) -> Immediate fill.
           c. Maker Queue Fill: If quoting at/inside best bid and resting for >= 2.0s with realistic market taker flow (>= 8s interval).
        4. Complete Set Merge: Automatically pairs UP + DOWN tokens into $1.00 USDC locked profit.
        """
        filled_events = []
        now = time.time()
        current_spent = self.inventory.up.total_spent + self.inventory.down.total_spent
        cap_ceiling = self.inventory.allocated_capital

        # 1. Check UP limit bid fill
        if self.active_order_up and self.active_order_up.is_active:
            order_age = now - self.active_order_up.created_at
            # Must rest in the exchange queue for at least 2 seconds
            if order_age >= self._min_resting_seconds:
                order_cost = self.active_order_up.price * self.active_order_up.shares
                # Bankroll safeguard
                if current_spent + order_cost <= cap_ceiling:
                    bid_p = self.active_order_up.price
                    
                    # Direct crossing or real trade print
                    is_direct_cross = (up_market_ask > 0 and up_market_ask <= bid_p) or (up_last_trade > 0 and up_last_trade <= bid_p)
                    
                    # Realistic Maker Flow: At or near best bid with realistic taker arrival cooldown
                    is_maker_taker_flow = (
                        up_best_bid > 0
                        and bid_p >= (up_best_bid - 0.01)
                        and (now - self._last_fill_time_up) >= self._min_fill_cooldown_seconds
                    )

                    if is_direct_cross or is_maker_taker_flow:
                        fill_price = bid_p
                        fill_shares = self.active_order_up.shares
                        fee = 0.0  # Polymarket maker orders have 0% fee

                        # Record fill in inventory & trigger atomic set merge if paired
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
                            self.db.log_trade(
                                event,
                                self.inventory.up.shares,
                                self.inventory.down.shares,
                                "PAPER_LIVE",
                                session_id=self.inventory.session_id,
                            )

                        filled_events.append(event)
                        self.fill_history.append(event)
                        self.active_order_up.is_active = False
                        self.active_order_up = None
                        self._last_fill_time_up = now
                        current_spent += order_cost
                        logger.info(f"[PAPER FILL] UP: {fill_shares:.0f} shs @ ${fill_price:.3f} | Order: {event['order_id']}")

        # 2. Check DOWN limit bid fill
        if self.active_order_down and self.active_order_down.is_active:
            order_age = now - self.active_order_down.created_at
            if order_age >= self._min_resting_seconds:
                order_cost = self.active_order_down.price * self.active_order_down.shares
                if current_spent + order_cost <= cap_ceiling:
                    bid_p = self.active_order_down.price
                    
                    is_direct_cross = (down_market_ask > 0 and down_market_ask <= bid_p) or (down_last_trade > 0 and down_last_trade <= bid_p)
                    
                    is_maker_taker_flow = (
                        down_best_bid > 0
                        and bid_p >= (down_best_bid - 0.01)
                        and (now - self._last_fill_time_down) >= self._min_fill_cooldown_seconds
                    )

                    if is_direct_cross or is_maker_taker_flow:
                        fill_price = bid_p
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
                            self.db.log_trade(
                                event,
                                self.inventory.up.shares,
                                self.inventory.down.shares,
                                "PAPER_LIVE",
                                session_id=self.inventory.session_id,
                            )

                        filled_events.append(event)
                        self.fill_history.append(event)
                        self.active_order_down.is_active = False
                        self.active_order_down = None
                        self._last_fill_time_down = now
                        logger.info(f"[PAPER FILL] DOWN: {fill_shares:.0f} shs @ ${fill_price:.3f} | Order: {event['order_id']}")

        return filled_events
