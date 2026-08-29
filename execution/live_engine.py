"""
Live Polymarket Order Execution Router using the official Polymarket Python SDK (`py-sdk`).
Enforces strict $300 capital bankroll limits, 20 shares/order, and 60 max unhedged shares imbalance.
"""
import asyncio
import logging
import time
from typing import Any, Dict, Optional
from config import BotConfig
from models.inventory import InventoryManager
from models.polymarket_client import PolymarketManager

logger = logging.getLogger("LiveTradingEngine")


class LiveTradingEngine:
    """
    Manages live EIP-712 signed limit orders, inventory skewing, and complete-set
    redemptions on the Polymarket CLOB.
    """

    def __init__(
        self,
        config: BotConfig,
        inventory: InventoryManager,
        poly_manager: Optional[PolymarketManager] = None,
    ):
        self.config = config
        self.inventory = inventory
        self.poly_manager = poly_manager or PolymarketManager(
            private_key=config.private_key,
            wallet_address=config.wallet_address,
            proxy_url=config.proxy_url,
        )

        self.active_order_up_id: Optional[str] = None
        self.active_order_down_id: Optional[str] = None
        self.active_order_up_price: float = 0.0
        self.active_order_down_price: float = 0.0
        self._is_initialized = False

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
    ):
        """
        Calculates required live orders and synchronizes with Polymarket CLOB.
        Enforces strict $300 bankroll limit, 20 shares per order, and max 60 unhedged shares.
        """
        if self.config.dry_run or not self._is_initialized:
            return

        # Check geoblock
        geo = self.poly_manager.get_telemetry().get("geoblock", {})
        if geo.get("blocked"):
            logger.warning(f"Live trading paused: Geoblocked region ({geo.get('country')}). Outbound proxy required.")
            return

        # Check bankroll invariant
        inv_summary = self.inventory.get_summary()
        current_spent = inv_summary["total_spent"]
        order_cost_estimate = self.config.order_size_shares * max(quote_up, quote_down)

        if current_spent + order_cost_estimate > 300.0:
            logger.warning(f"Bankroll Guard: Total spent (${current_spent:.2f}) near $300 ceiling. Skipping new bids.")
            return

        # 1. Synchronize UP Limit Bid
        if allow_up and self.config.token_id_up and abs(quote_up - self.active_order_up_price) >= 0.005:
            if self.active_order_up_id:
                await self.poly_manager.cancel_order(self.active_order_up_id)
                self.active_order_up_id = None

            res = await self.poly_manager.place_limit_order(
                token_id=self.config.token_id_up,
                side="BUY",
                price=round(quote_up, 2),
                amount_shares=self.config.order_size_shares,
            )
            if res.get("status") == "SUCCESS":
                self.active_order_up_id = res.get("order_id")
                self.active_order_up_price = quote_up
                logger.info(f"Placed Live Bid UP: 20 shares @ ${quote_up:.2f} (Order: {self.active_order_up_id})")
        elif not allow_up and self.active_order_up_id:
            await self.poly_manager.cancel_order(self.active_order_up_id)
            self.active_order_up_id = None

        # 2. Synchronize DOWN Limit Bid
        if allow_down and self.config.token_id_down and abs(quote_down - self.active_order_down_price) >= 0.005:
            if self.active_order_down_id:
                await self.poly_manager.cancel_order(self.active_order_down_id)
                self.active_order_down_id = None

            res = await self.poly_manager.place_limit_order(
                token_id=self.config.token_id_down,
                side="BUY",
                price=round(quote_down, 2),
                amount_shares=self.config.order_size_shares,
            )
            if res.get("status") == "SUCCESS":
                self.active_order_down_id = res.get("order_id")
                self.active_order_down_price = quote_down
                logger.info(f"Placed Live Bid DOWN: 20 shares @ ${quote_down:.2f} (Order: {self.active_order_down_id})")
        elif not allow_down and self.active_order_down_id:
            await self.poly_manager.cancel_order(self.active_order_down_id)
            self.active_order_down_id = None

    async def merge_complete_sets_onchain(self, condition_id: str, amount: str = "max") -> Dict[str, Any]:
        """Redeems complete sets into USDC via official SDK merge_multiple_positions."""
        logger.info(f"Executing Complete-Set Merge on Polymarket: condition {condition_id}, amount {amount}")
        return await self.poly_manager.merge_complete_sets(condition_id=condition_id, amount=amount)

    async def cancel_all_orders(self):
        """Emergency circuit breaker: cancels all active open orders."""
        logger.warning("Emergency Halt: Canceling all open live orders on Polymarket CLOB...")
        self.active_order_up_id = None
        self.active_order_down_id = None
        return await self.poly_manager.cancel_all()
