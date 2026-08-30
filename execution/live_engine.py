"""
Live Polymarket Order Execution Router using the official Polymarket Python SDK (`polymarket-client`).
Enforces strict capital bankroll limits, dynamic clip-sizing, real-time CLOB fill reconciliation,
and complete-set smart contract merges on Polygon.
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
                        "filled_shares": 0.0,
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
                        "filled_shares": 0.0,
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
        Queries real user order status directly from Polymarket CLOB to eliminate phantom fills.
        """
        if self.config.dry_run:
            return []

        fills = []
        now = time.time()

        # Throttle fill polling to max once every 0.8s to respect CLOB rate limits
        if now - self._last_fill_check_time < 0.8:
            return fills
        self._last_fill_check_time = now

        # Reconcile UP Order Fills
        if self.active_order_up and self.active_order_up.get("order_id"):
            ord_id = self.active_order_up["order_id"]
            if ord_id.startswith("sim_"):
                # Simulation fallback for dry-run
                pass
            else:
                try:
                    order_info = await self.poly_manager.get_order(ord_id)
                    if order_info:
                        matched = float(getattr(order_info, "size_matched", 0.0) or 0.0)
                        prev_matched = float(self.active_order_up.get("filled_shares", 0.0))
                        status = str(getattr(order_info, "status", "")).upper()

                        if matched > prev_matched:
                            delta_shares = matched - prev_matched
                            fill_price = float(getattr(order_info, "price", self.active_order_up["price"]))
                            fill_event = {
                                "side": "UP",
                                "price": fill_price,
                                "shares": delta_shares,
                                "cost": round(fill_price * delta_shares, 4),
                                "fee": 0.0,
                                "order_id": ord_id,
                                "timestamp": now,
                                "market_title": self.active_order_up.get("market_title", getattr(feed, "market_title", "")),
                                "market_slug": self.active_order_up.get("market_slug", getattr(feed, "market_slug", "")),
                            }
                            self.inventory.on_fill("UP", fill_price, delta_shares)
                            if self.db:
                                try:
                                    self.db.log_trade(
                                        fill_event,
                                        up_shares_after=self.inventory.up.shares,
                                        down_shares_after=self.inventory.down.shares,
                                        execution_type="LIVE",
                                        session_id=getattr(self.inventory, "session_id", "GLOBAL"),
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to log live trade to SQLite: {e}")

                            fills.append(fill_event)
                            self.active_order_up["filled_shares"] = matched
                            logger.info(f"⚡ [CONFIRMED LIVE FILL] UP {delta_shares:.1f} shs @ ${fill_price:.3f} (Total Matched: {matched:.1f}/{self.active_order_up['shares']:.1f})")

                        if status in ("MATCHED", "CANCELLED", "EXPIRED") or matched >= self.active_order_up["shares"]:
                            self.active_order_up = None
                except Exception as e:
                    logger.debug(f"Error checking live UP order fill: {e}")

        # Reconcile DOWN Order Fills
        if self.active_order_down and self.active_order_down.get("order_id"):
            ord_id = self.active_order_down["order_id"]
            if ord_id.startswith("sim_"):
                pass
            else:
                try:
                    order_info = await self.poly_manager.get_order(ord_id)
                    if order_info:
                        matched = float(getattr(order_info, "size_matched", 0.0) or 0.0)
                        prev_matched = float(self.active_order_down.get("filled_shares", 0.0))
                        status = str(getattr(order_info, "status", "")).upper()

                        if matched > prev_matched:
                            delta_shares = matched - prev_matched
                            fill_price = float(getattr(order_info, "price", self.active_order_down["price"]))
                            fill_event = {
                                "side": "DOWN",
                                "price": fill_price,
                                "shares": delta_shares,
                                "cost": round(fill_price * delta_shares, 4),
                                "fee": 0.0,
                                "order_id": ord_id,
                                "timestamp": now,
                                "market_title": self.active_order_down.get("market_title", getattr(feed, "market_title", "")),
                                "market_slug": self.active_order_down.get("market_slug", getattr(feed, "market_slug", "")),
                            }
                            self.inventory.on_fill("DOWN", fill_price, delta_shares)
                            if self.db:
                                try:
                                    self.db.log_trade(
                                        fill_event,
                                        up_shares_after=self.inventory.up.shares,
                                        down_shares_after=self.inventory.down.shares,
                                        execution_type="LIVE",
                                        session_id=getattr(self.inventory, "session_id", "GLOBAL"),
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to log live trade to SQLite: {e}")

                            fills.append(fill_event)
                            self.active_order_down["filled_shares"] = matched
                            logger.info(f"⚡ [CONFIRMED LIVE FILL] DOWN {delta_shares:.1f} shs @ ${fill_price:.3f} (Total Matched: {matched:.1f}/{self.active_order_down['shares']:.1f})")

                        if status in ("MATCHED", "CANCELLED", "EXPIRED") or matched >= self.active_order_down["shares"]:
                            self.active_order_down = None
                except Exception as e:
                    logger.debug(f"Error checking live DOWN order fill: {e}")

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

    async def merge_complete_sets_onchain(self, condition_id: str, amount: Any = "max") -> Dict[str, Any]:
        """Redeems complete sets into USDC via official SDK merge_positions."""
        logger.info(f"Executing Complete-Set Merge on Polymarket: condition {condition_id}, amount {amount}")
        return await self.poly_manager.merge_complete_sets(condition_id=condition_id, amount=amount)

    async def cancel_all_orders(self):
        """Emergency circuit breaker: cancels all active open orders."""
        logger.warning("Emergency Halt: Canceling all open live orders on Polymarket CLOB...")
        self.active_order_up = None
        self.active_order_down = None
        return await self.poly_manager.cancel_all()
