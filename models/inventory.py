"""
Inventory and Complete-Set Manager with Avellaneda-Stoikov Skew and Daily Stop-Loss Circuit Breaker.
"""
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class Position:
    shares: float = 0.0
    avg_cost: float = 0.0
    total_spent: float = 0.0

    def add_fill(self, shares: float, price: float):
        if shares <= 0:
            return
        self.total_spent += shares * price
        self.shares += shares
        self.avg_cost = self.total_spent / self.shares if self.shares > 0 else 0.0

    def reduce_shares(self, shares: float):
        if shares <= 0 or self.shares <= 0:
            return
        shares_to_remove = min(shares, self.shares)
        self.total_spent -= shares_to_remove * self.avg_cost
        self.shares -= shares_to_remove
        if self.shares <= 0:
            self.shares = 0.0
            self.avg_cost = 0.0
            self.total_spent = 0.0


class InventoryManager:
    """
    Tracks UP and DOWN token positions, calculates Complete Sets formed ($1.00 CTF merges),
    banks locked arbitrage profits, computes Avellaneda-Stoikov inventory skew,
    and enforces an automated Daily Stop-Loss Circuit Breaker.
    """

    def __init__(
        self,
        gamma: float = 0.003,
        max_imbalance: float = 100.0,
        daily_stop_loss: float = 30.0,
    ):
        self.gamma = gamma                    # Inventory risk penalty factor
        self.max_imbalance = max_imbalance    # Max allowable unhedged position (100 shares)
        self.daily_stop_loss = daily_stop_loss # Max allowable daily drawdown ($30.00)

        self.up = Position()
        self.down = Position()

        # Cumulative PnL tracking
        self.total_complete_sets_merged: float = 0.0
        self.realized_arbitrage_pnl: float = 0.0
        self.total_fees_paid: float = 0.0
        self.is_stop_loss_triggered: bool = False

    def on_fill(self, side: str, price: float, shares: float, fee: float = 0.0):
        """Processes an executed limit order fill."""
        self.total_fees_paid += fee
        side_upper = side.upper()

        if side_upper == "UP":
            self.up.add_fill(shares, price)
        elif side_upper == "DOWN":
            self.down.add_fill(shares, price)

        # Check if complete sets can be formed and merged
        self._check_and_merge_complete_sets()
        self._check_stop_loss()

    def _check_and_merge_complete_sets(self):
        """
        Merges matching UP and DOWN tokens into complete sets (1 UP + 1 DOWN = $1.00 USDC).
        Locks in risk-free profit immediately.
        """
        matchable_sets = min(self.up.shares, self.down.shares)

        if matchable_sets >= 1.0:
            combined_cost_per_set = self.up.avg_cost + self.down.avg_cost
            net_profit_per_set = 1.00 - combined_cost_per_set

            total_profit_locked = matchable_sets * net_profit_per_set
            self.realized_arbitrage_pnl += total_profit_locked
            self.total_complete_sets_merged += matchable_sets

            logger.info(
                f"[COMPLETE SET MERGED] {matchable_sets:.1f} sets merged @ avg cost ${combined_cost_per_set:.3f} | "
                f"Locked Profit: +${total_profit_locked:.2f} | "
                f"Cumulative PnL: +${self.realized_arbitrage_pnl:.2f}"
            )

            # Deduct merged tokens from inventory
            self.up.reduce_shares(matchable_sets)
            self.down.reduce_shares(matchable_sets)

    def _check_stop_loss(self):
        """Emergency circuit breaker: shuts down quoting if daily loss reaches stop-loss threshold."""
        net_pnl = self.realized_arbitrage_pnl - self.total_fees_paid
        if net_pnl <= -self.daily_stop_loss:
            self.is_stop_loss_triggered = True
            logger.critical(
                f"🚨 [DAILY STOP-LOSS TRIGGERED] Net PnL reached -${abs(net_pnl):.2f} "
                f"(Threshold: -${self.daily_stop_loss:.2f}). Halting all trading activity!"
            )

    @property
    def net_imbalance(self) -> float:
        """Shares UP - Shares DOWN. Positive = Long UP, Negative = Long DOWN."""
        return self.up.shares - self.down.shares

    def get_stoikov_skew(self) -> float:
        """
        Calculates price adjustment (skew in cents) based on current inventory imbalance.
        """
        imbalance = self.net_imbalance
        skew = self.gamma * imbalance
        return max(-0.05, min(0.05, skew))

    def get_summary(self) -> dict:
        """Returns snapshot of current inventory and PnL."""
        net_pnl = self.realized_arbitrage_pnl - self.total_fees_paid
        return {
            "up_shares": round(self.up.shares, 1),
            "up_avg_cost": round(self.up.avg_cost, 3),
            "down_shares": round(self.down.shares, 1),
            "down_avg_cost": round(self.down.avg_cost, 3),
            "net_imbalance": round(self.net_imbalance, 1),
            "complete_sets_merged": round(self.total_complete_sets_merged, 1),
            "realized_arb_pnl": round(self.realized_arbitrage_pnl, 2),
            "fees_paid": round(self.total_fees_paid, 2),
            "net_pnl": round(net_pnl, 2),
            "is_stop_loss_triggered": self.is_stop_loss_triggered,
        }
