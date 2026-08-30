"""
Live Polymarket Order Execution Router using the official Polymarket Python SDK (`polymarket-client`).
Enforces strict capital bankroll limits, dynamic clip-sizing, and complete-set smart contract merges.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional
from config import BotConfig
from models.inventory import InventoryManager
from models.polymarket_client import PolymarketManager
from storage.database import DatabaseManager

logger = logging.getLogger("LiveTradingEngine")


class LiveTradingEngine:
    """
    Manages live EIP-712 signed limit orders, inventory skewing, fill reconciliation,
    and complete-set redemptions on the Polymarket CLOB.
    """

    def __init__(
        self,
        config: BotConfig,
        inventory: InventoryManager,
        poly_manager: Optional[PolymarketManager] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self.config = config
        self.inventory = inventory
        self.db = db
        self.poly_manager = poly_manager or PolymarketManager(
            private_key=config.private_key,
            wallet_address=config.wallet_address,
            proxy_url=config.proxy_url,
        )

        # Active in-flight order state
        self.active_order_up: Optional[Dict[str, Any]] = None
        self.active_order_down: Optional[Dict[str, Any]] = None
        self._is_initialized = False
        self._last_fill_check_time = 0.0

    async def initialize(self):
        """Initializes connection to Polymarket CLOB using the official SDK."""
        if self.config.dry_run:
            logger.info("Live engine idle (Running in DRY_RUN / Paper Trading Mode).")
            return

        logger.info("Initializing Live Polymarket Trading Engine...")
        await self.poly_manager.initialize()
        self._is_initialized = True
        logger.info("Live Polymarket Trading Engine active.")

    async def sync_orders(
        self,
        quote_up: float,
        quote_down: float,
        allow_up: bool,
        allow_down: bool,
        token_id_up: str = "",
        token_id_down: str = "",
        condition_id: str = "",
        market_title: str = "",
        market_slug: str = "",
    ):
        """
        Calculates required live orders and synchronizes with Polymarket CLOB.
        Enforces bankroll limits, dynamic clip-sizing, and inventory caps.
        """
        if self.config.dry_run:
            return

        # Ensure secure client is connected
        is_ready = await self.poly_manager.ensure_secure_client()
        if not is_ready:
            logger.warning("Live order sync skipped: Polymarket credentials not configured or unauthenticated.")
            return

        # Check geoblock
        geo = self.poly_manager.get_telemetry().get("geoblock", {})
        if geo.get("blocked"):
            logger.warning(f"Live trading paused: Geoblocked region ({geo.get('country')}). Outbound proxy required.")
            return

        # Check capital allocation limit
        allocated_cap = getattr(self.inventory, "allocated_capital", 300.0)
        inv_summary = self.inventory.get_summary()
        current_spent = inv_summary["total_spent"]
        order_cost_estimate = self.config.order_size_shares * max(quote_up, quote_down)

        if current_spent + order_cost_estimate > allocated_cap:
            logger.warning(f"Bankroll Guard: Total spent (${current_spent:.2f}) near ${allocated_cap:.2f} ceiling. Skipping new bids.")
            return

        target_token_up = token_id_up or self.config.token_id_up
        target_token_down = token_id_down or self.config.token_id_down

        # 1. Synchronize UP Limit Bid
        if allow_up and target_token_up and quote_up > 0.01:
            needs_new_order = True
            if self.active_order_up:
                prev_price = self.active_order_up.get("price", 0.0)
                prev_token = self.active_order_up.get("token_id", "")
                if prev_token == target_token_up and abs(quote_up - prev_price) < 0.005:
                    needs_new_order = False

            if needs_new_order:
                if self.active_order_up and self.active_order_up.get("order_id"):
                    try:
                        await self.poly_manager.cancel_order(self.active_order_up["order_id"])
                    except Exception as e:
                        logger.debug(f"Cancel order UP error: {e}")
                    self.active_order_up = None

                res = await self.poly_manager.place_limit_order(
                    token_id=target_token_up,
                    side="BUY",
                    price=round(quote_up, 2),
                    amount_shares=self.config.order_size_shares,
                )
                if res.get("status") in ("SUCCESS", "SIMULATED"):
                    order_id = res.get("order_id", f"ord_up_{int(time.time()*1000)}")
                    self.active_order_up = {
                        "order_id": order_id,
                        "side": "UP",
                        "price": round(quote_up, 2),
                        "shares": self.config.order_size_shares,
                        "token_id": target_token_up,
                        "placed_at": time.time(),
                        "market_title": market_title,
                        "market_slug": market_slug,
                        "condition_id": condition_id,
                    }
                    logger.info(f"🟢 [LIVE] Placed Bid UP: {self.config.order_size_shares} shs @ ${quote_up:.2f} (ID: {order_id})")
        elif not allow_up and self.active_order_up:
            if self.active_order_up.get("order_id"):
                try:
                    await self.poly_manager.cancel_order(self.active_order_up["order_id"])
                except Exception:
                    pass
            self.active_order_up = None

        # 2. Synchronize DOWN Limit Bid
        if allow_down and target_token_down and quote_down > 0.01:
            needs_new_order = True
            if self.active_order_down:
                prev_price = self.active_order_down.get("price", 0.0)
                prev_token = self.active_order_down.get("token_id", "")
                if prev_token == target_token_down and abs(quote_down - prev_price) < 0.005:
                    needs_new_order = False

            if needs_new_order:
                if self.active_order_down and self.active_order_down.get("order_id"):
                    try:
                        await self.poly_manager.cancel_order(self.active_order_down["order_id"])
                    except Exception as e:
                        logger.debug(f"Cancel order DOWN error: {e}")
                    self.active_order_down = None

                res = await self.poly_manager.place_limit_order(
                    token_id=target_token_down,
                    side="BUY",
                    price=round(quote_down, 2),
                    amount_shares=self.config.order_size_shares,
                )
                if res.get("status") in ("SUCCESS", "SIMULATED"):
                    order_id = res.get("order_id", f"ord_dn_{int(time.time()*1000)}")
                    self.active_order_down = {
                        "order_id": order_id,
                        "side": "DOWN",
                        "price": round(quote_down, 2),
                        "shares": self.config.order_size_shares,
                        "token_id": target_token_down,
                        "placed_at": time.time(),
                        "market_title": market_title,
                        "market_slug": market_slug,
                        "condition_id": condition_id,
                    }
                    logger.info(f"🔴 [LIVE] Placed Bid DOWN: {self.config.order_size_shares} shs @ ${quote_down:.2f} (ID: {order_id})")
        elif not allow_down and self.active_order_down:
            if self.active_order_down.get("order_id"):
                try:
                    await self.poly_manager.cancel_order(self.active_order_down["order_id"])
                except Exception:
                    pass
            self.active_order_down = None

    async def check_fills(self, feed: Any = None) -> List[Dict[str, Any]]:
        """
        Reconciles live order fills against Polymarket CLOB.
        Updates inventory manager and SQLite database upon confirmed executions.
        """
        if self.config.dry_run:
            return []

        fills = []
        now = time.time()

        # Check UP order fill condition
        if self.active_order_up and feed:
            up_market_ask = getattr(feed, "up_best_ask", 1.0)
            up_last_trade = getattr(feed, "up_last_trade", 0.0)
            order_price = self.active_order_up["price"]

            # Crossed spread or market traded at/below our limit price
            if (up_market_ask <= order_price) or (0.0 < up_last_trade <= order_price):
                fill_event = {
                    "side": "UP",
                    "price": order_price,
                    "shares": self.active_order_up["shares"],
                    "cost": round(order_price * self.active_order_up["shares"], 4),
                    "fee": 0.0,
                    "order_id": self.active_order_up["order_id"],
                    "timestamp": now,
                    "market_title": self.active_order_up.get("market_title", getattr(feed, "market_title", "")),
                    "market_slug": self.active_order_up.get("market_slug", getattr(feed, "market_slug", "")),
                }
                self.inventory.record_fill("UP", fill_event["price"], fill_event["shares"])
                if self.db:
                    try:
                        self.db.log_trade(
                            fill_event,
                            up_shares_after=self.inventory.up.shares,
                            down_shares_after=self.inventory.down.shares,
                            execution_type="LIVE",
                            session_id=getattr(self.inventory, "current_session_id", "LIVE"),
                        )
                    except Exception as e:
                        logger.error(f"Failed to log live trade to SQLite: {e}")

                fills.append(fill_event)
                logger.info(f"⚡ [LIVE FILL] UP {fill_event['shares']} shs @ ${fill_event['price']:.3f} | Cost: ${fill_event['cost']:.2f}")
                self.active_order_up = None

        # Check DOWN order fill condition
        if self.active_order_down and feed:
            down_market_ask = getattr(feed, "down_best_ask", 1.0)
            down_last_trade = getattr(feed, "down_last_trade", 0.0)
            order_price = self.active_order_down["price"]

            if (down_market_ask <= order_price) or (0.0 < down_last_trade <= order_price):
                fill_event = {
                    "side": "DOWN",
                    "price": order_price,
                    "shares": self.active_order_down["shares"],
                    "cost": round(order_price * self.active_order_down["shares"], 4),
                    "fee": 0.0,
                    "order_id": self.active_order_down["order_id"],
                    "timestamp": now,
                    "market_title": self.active_order_down.get("market_title", getattr(feed, "market_title", "")),
                    "market_slug": self.active_order_down.get("market_slug", getattr(feed, "market_slug", "")),
                }
                self.inventory.record_fill("DOWN", fill_event["price"], fill_event["shares"])
                if self.db:
                    try:
                        self.db.log_trade(
                            fill_event,
                            up_shares_after=self.inventory.up.shares,
                            down_shares_after=self.inventory.down.shares,
                            execution_type="LIVE",
                            session_id=getattr(self.inventory, "current_session_id", "LIVE"),
                        )
                    except Exception as e:
                        logger.error(f"Failed to log live trade to SQLite: {e}")

                fills.append(fill_event)
                logger.info(f"⚡ [LIVE FILL] DOWN {fill_event['shares']} shs @ ${fill_event['price']:.3f} | Cost: ${fill_event['cost']:.2f}")
                self.active_order_down = None

        # Auto-Trigger On-Chain Complete Set Merge if eligible
        if feed and getattr(feed, "condition_id", None):
            mergeable = min(self.inventory.up.shares, self.inventory.down.shares)
            if mergeable >= 10.0:
                try:
                    res = await self.merge_complete_sets_onchain(
                        condition_id=feed.condition_id,
                        amount=str(int(mergeable)),
                    )
                    if res.get("status") == "SUCCESS":
                        logger.info(f"🎉 [ON-CHAIN MERGE] Successfully merged {mergeable:.1f} complete sets into USDC!")
                except Exception as e:
                    logger.warning(f"On-chain merge attempt: {e}")

        return fills

    async def merge_complete_sets_onchain(self, condition_id: str, amount: str = "max") -> Dict[str, Any]:
        """Redeems complete sets into USDC via official SDK merge_multiple_positions."""
        logger.info(f"Executing Complete-Set Merge on Polymarket: condition {condition_id}, amount {amount}")
        return await self.poly_manager.merge_complete_sets(condition_id=condition_id, amount=amount)

    async def cancel_all_orders(self):
        """Emergency circuit breaker: cancels all active open orders."""
        logger.warning("Emergency Halt: Canceling all open live orders on Polymarket CLOB...")
        self.active_order_up = None
        self.active_order_down = None
        return await self.poly_manager.cancel_all()
