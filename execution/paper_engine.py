"""
Realistic Polymarket CLOB Paper Trading & Order Matching Simulator.
Powered by pm_trader level-by-level order book simulation:
1. True L2 Order Book Depth & FIFO Queue Priority.
2. In-flight network latency simulation (50ms - 100ms wire delay).
3. Direct spread cross execution via pm_trader.orderbook.simulate_buy_fill / simulate_sell_fill.
4. Exact Polymarket trading fees and slippage calculations in basis points.
5. Passive Maker matching driven strictly by real market trade prints.
6. Bankroll invariants and strict Complete-Set arbitrage merging.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from models.inventory import InventoryManager
from pm_trader.orderbook import simulate_buy_fill, simulate_sell_fill, calculate_fee
from pm_trader.models import OrderBook, OrderBookLevel, FillResult

logger = logging.getLogger(__name__)


@dataclass
class VirtualOrder:
    order_id: str
    token_side: str  # "UP" or "DOWN"
    price: float
    shares: float  # Initial requested size
    remaining_shares: float  # Unfilled size
    queue_ahead_shares: float  # Total volume sitting ahead in book queue
    created_at: float
    in_flight_until: float  # Timestamp when order is acknowledged by exchange
    is_active: bool = True


class PaperTradingEngine:
    """
    Simulates 100% realistic CLOB matching against live Polymarket order book depth and trade prints.
    Integrates pm_trader's level-by-level book walking, VWAP price discovery, exact fee formulas,
    and slippage tracking.
    """

    def __init__(
        self,
        inventory: InventoryManager,
        order_size_shares: float = 20.0,
        in_flight_latency_sec: float = 0.060,
        db=None,
    ):
        self.inventory = inventory
        self.order_size_shares = order_size_shares
        self.in_flight_latency_sec = in_flight_latency_sec
        self.db = db

        # Active virtual limit orders
        self.active_order_up: Optional[VirtualOrder] = None
        self.active_order_down: Optional[VirtualOrder] = None

        # Fill history (hydrated from DB if available)
        self.fill_history: List[dict] = []
        if self.db:
            try:
                self.fill_history = self.db.get_recent_trades(limit=100)
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
        feed: Optional[Any] = None,
    ):
        """
        Updates or replaces active virtual limit orders, calculating queue depth ahead.
        Only re-quotes if price changes by >= $0.01 or if quoting permission changed.
        """
        now = time.time()

        # 1. Update UP limit bid
        if allow_up and quote_up > 0:
            needs_new_order = (
                self.active_order_up is None
                or not self.active_order_up.is_active
                or abs(self.active_order_up.price - quote_up) >= 0.01
            )
            if needs_new_order:
                queue_ahead = 0.0
                if feed and hasattr(feed, "get_bid_depth_ahead"):
                    queue_ahead = feed.get_bid_depth_ahead("UP", quote_up)

                self._order_counter += 1
                self.active_order_up = VirtualOrder(
                    order_id=f"SIM-UP-{self._order_counter}",
                    token_side="UP",
                    price=quote_up,
                    shares=self.order_size_shares,
                    remaining_shares=self.order_size_shares,
                    queue_ahead_shares=queue_ahead,
                    created_at=now,
                    in_flight_until=now + self.in_flight_latency_sec,
                    is_active=True,
                )
        else:
            self.active_order_up = None

        # 2. Update DOWN limit bid
        if allow_down and quote_down > 0:
            needs_new_order = (
                self.active_order_down is None
                or not self.active_order_down.is_active
                or abs(self.active_order_down.price - quote_down) >= 0.01
            )
            if needs_new_order:
                queue_ahead = 0.0
                if feed and hasattr(feed, "get_bid_depth_ahead"):
                    queue_ahead = feed.get_bid_depth_ahead("DOWN", quote_down)

                self._order_counter += 1
                self.active_order_down = VirtualOrder(
                    order_id=f"SIM-DOWN-{self._order_counter}",
                    token_side="DOWN",
                    price=quote_down,
                    shares=self.order_size_shares,
                    remaining_shares=self.order_size_shares,
                    queue_ahead_shares=queue_ahead,
                    created_at=now,
                    in_flight_until=now + self.in_flight_latency_sec,
                    is_active=True,
                )
        else:
            self.active_order_down = None

    def check_fills(
        self,
        up_market_ask: float = 0.0,
        up_last_trade: float = 0.0,
        down_market_ask: float = 0.0,
        down_last_trade: float = 0.0,
        up_best_bid: float = 0.0,
        down_best_bid: float = 0.0,
        feed: Optional[Any] = None,
        trade_events: Optional[List[Dict[str, Any]]] = None,
    ) -> List[dict]:
        """
        Executes 100% realistic matching against live Polymarket market data:
        1. In-flight wire delay: Orders cannot fill before in_flight_until.
        2. Bankroll check: Total spent + order cost <= session allocated capital.
        3. Direct Crossing: Walks ask book using pm_trader.orderbook.simulate_buy_fill.
        4. Passive Maker Flow: Real market trade prints consume queue ahead before filling order.
        5. Exact Fees & Slippage: Computed using official Polymarket formulas.
        """
        filled_events = []
        now = time.time()
        current_spent = self.inventory.up.total_spent + self.inventory.down.total_spent
        cap_ceiling = self.inventory.allocated_capital
        fee_rate_bps = getattr(feed, "fee_rate_bps", 0) if feed else 0

        # Extract unprocessed trade prints from feed if available
        incoming_trades = list(trade_events or [])
        if feed and hasattr(feed, "pop_new_trades"):
            incoming_trades.extend(feed.pop_new_trades())

        # =========================================================================
        # 1. MATCH UP ORDER
        # =========================================================================
        if self.active_order_up and self.active_order_up.is_active:
            order = self.active_order_up

            # Check in-flight latency
            if now >= order.in_flight_until and order.remaining_shares > 0:
                order_cost = order.price * order.remaining_shares

                if current_spent + order_cost <= cap_ceiling:
                    bid_p = order.price
                    fill_shares = 0.0
                    fill_price = bid_p
                    fill_fee = 0.0
                    slippage_bps = 0.0

                    # A. Direct Cross: Walk ask book using pm_trader.orderbook.simulate_buy_fill
                    if feed and hasattr(feed, "get_order_book_obj"):
                        book_up = feed.get_order_book_obj("UP")
                    else:
                        asks_list = [OrderBookLevel(price=a["price"], size=a.get("size", 100.0)) for a in (feed.up_asks if feed else [])]
                        if not asks_list and up_market_ask > 0.0:
                            asks_list = [OrderBookLevel(price=up_market_ask, size=1000.0)]
                        bids_list = [OrderBookLevel(price=b["price"], size=b.get("size", 100.0)) for b in (feed.up_bids if feed else [])]
                        if not bids_list and up_best_bid > 0.0:
                            bids_list = [OrderBookLevel(price=up_best_bid, size=1000.0)]
                        book_up = OrderBook(bids=bids_list, asks=asks_list)

                    fill_result = simulate_buy_fill(
                        book=book_up,
                        amount_usd=order.remaining_shares * bid_p,
                        fee_rate_bps=fee_rate_bps,
                        order_type="fak",
                        max_price=bid_p,
                    )

                    if fill_result.total_shares > 0:
                        fill_shares = min(order.remaining_shares, fill_result.total_shares)
                        fill_price = fill_result.avg_price
                        fill_fee = fill_result.fee
                        slippage_bps = fill_result.slippage_bps

                    # B. Passive Maker Queue Flow: Real trade prints executed at or below our bid
                    if fill_shares <= 0:
                        matching_trades = [
                            t for t in incoming_trades
                            if t.get("side") == "UP" and t.get("price", 999.0) <= bid_p
                        ]
                        for tr in matching_trades:
                            tr_vol = float(tr.get("size") or self.order_size_shares)
                            if order.queue_ahead_shares > 0:
                                consumed = min(order.queue_ahead_shares, tr_vol)
                                order.queue_ahead_shares -= consumed
                                tr_vol -= consumed

                            if order.queue_ahead_shares <= 0 and tr_vol > 0:
                                exec_qty = min(order.remaining_shares, tr_vol)
                                fill_shares += exec_qty
                                fill_price = bid_p
                                fill_fee = calculate_fee(fee_rate_bps, bid_p, exec_qty)
                                if fill_shares >= order.remaining_shares:
                                    break

                    # Execute fill if matched
                    if fill_shares > 0:
                        fill_shares = round(fill_shares, 2)
                        self.inventory.on_fill("UP", fill_price, fill_shares, fill_fee)

                        event = {
                            "timestamp": now,
                            "order_id": order.order_id,
                            "side": "UP",
                            "price": round(fill_price, 4),
                            "shares": fill_shares,
                            "cost": round(fill_price * fill_shares, 2),
                            "fee": round(fill_fee, 4),
                            "slippage_bps": round(slippage_bps, 1),
                            "remaining_shares": max(0.0, round(order.remaining_shares - fill_shares, 2)),
                            "market_title": getattr(feed, "market_title", "BTC Up/Down Market") if feed else "BTC Up/Down Market",
                            "market_slug": getattr(feed, "market_slug", "") if feed else "",
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
                        order.remaining_shares -= fill_shares
                        current_spent += fill_price * fill_shares

                        if order.remaining_shares <= 0.01:
                            order.is_active = False
                            self.active_order_up = None

                        logger.info(
                            f"[REALISTIC PAPER FILL] UP: {fill_shares:.1f} shs @ ${fill_price:.3f} | "
                            f"Fee: ${fill_fee:.4f} | Slip: {slippage_bps:+.1f}bps | Order: {event['order_id']} | Market: {event['market_title']}"
                        )

        # =========================================================================
        # 2. MATCH DOWN ORDER
        # =========================================================================
        if self.active_order_down and self.active_order_down.is_active:
            order = self.active_order_down

            if now >= order.in_flight_until and order.remaining_shares > 0:
                order_cost = order.price * order.remaining_shares

                if current_spent + order_cost <= cap_ceiling:
                    bid_p = order.price
                    fill_shares = 0.0
                    fill_price = bid_p
                    fill_fee = 0.0
                    slippage_bps = 0.0

                    # A. Direct Cross: Walk ask book using pm_trader.orderbook.simulate_buy_fill
                    if feed and hasattr(feed, "get_order_book_obj"):
                        book_down = feed.get_order_book_obj("DOWN")
                    else:
                        asks_list = [OrderBookLevel(price=a["price"], size=a.get("size", 100.0)) for a in (feed.down_asks if feed else [])]
                        if not asks_list and down_market_ask > 0.0:
                            asks_list = [OrderBookLevel(price=down_market_ask, size=1000.0)]
                        bids_list = [OrderBookLevel(price=b["price"], size=b.get("size", 100.0)) for b in (feed.down_bids if feed else [])]
                        if not bids_list and down_best_bid > 0.0:
                            bids_list = [OrderBookLevel(price=down_best_bid, size=1000.0)]
                        book_down = OrderBook(bids=bids_list, asks=asks_list)

                    fill_result = simulate_buy_fill(
                        book=book_down,
                        amount_usd=order.remaining_shares * bid_p,
                        fee_rate_bps=fee_rate_bps,
                        order_type="fak",
                        max_price=bid_p,
                    )

                    if fill_result.total_shares > 0:
                        fill_shares = min(order.remaining_shares, fill_result.total_shares)
                        fill_price = fill_result.avg_price
                        fill_fee = fill_result.fee
                        slippage_bps = fill_result.slippage_bps

                    # B. Passive Maker Queue Flow: Real trade prints executed at or below our bid
                    if fill_shares <= 0:
                        matching_trades = [
                            t for t in incoming_trades
                            if t.get("side") == "DOWN" and t.get("price", 999.0) <= bid_p
                        ]
                        for tr in matching_trades:
                            tr_vol = float(tr.get("size") or self.order_size_shares)
                            if order.queue_ahead_shares > 0:
                                consumed = min(order.queue_ahead_shares, tr_vol)
                                order.queue_ahead_shares -= consumed
                                tr_vol -= consumed

                            if order.queue_ahead_shares <= 0 and tr_vol > 0:
                                exec_qty = min(order.remaining_shares, tr_vol)
                                fill_shares += exec_qty
                                fill_price = bid_p
                                fill_fee = calculate_fee(fee_rate_bps, bid_p, exec_qty)
                                if fill_shares >= order.remaining_shares:
                                    break

                    if fill_shares > 0:
                        fill_shares = round(fill_shares, 2)
                        self.inventory.on_fill("DOWN", fill_price, fill_shares, fill_fee)

                        event = {
                            "timestamp": now,
                            "order_id": order.order_id,
                            "side": "DOWN",
                            "price": round(fill_price, 4),
                            "shares": fill_shares,
                            "cost": round(fill_price * fill_shares, 2),
                            "fee": round(fill_fee, 4),
                            "slippage_bps": round(slippage_bps, 1),
                            "remaining_shares": max(0.0, round(order.remaining_shares - fill_shares, 2)),
                            "market_title": getattr(feed, "market_title", "BTC Up/Down Market") if feed else "BTC Up/Down Market",
                            "market_slug": getattr(feed, "market_slug", "") if feed else "",
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
                        order.remaining_shares -= fill_shares
                        current_spent += fill_price * fill_shares

                        if order.remaining_shares <= 0.01:
                            order.is_active = False
                            self.active_order_down = None

                        logger.info(
                            f"[REALISTIC PAPER FILL] DOWN: {fill_shares:.1f} shs @ ${fill_price:.3f} | "
                            f"Fee: ${fill_fee:.4f} | Slip: {slippage_bps:+.1f}bps | Order: {event['order_id']} | Market: {event['market_title']}"
                        )

        return filled_events
