"""
Inventory and Complete-Set Manager with Avellaneda-Stoikov Skew and Daily Stop-Loss Circuit Breaker.
"""
from dataclasses import dataclass, field
from typing import Optional
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
    enforces an automated Daily Stop-Loss Circuit Breaker, and persists all state to SQLite.
    """

    def __init__(
        self,
        gamma: float = 0.003,
        max_imbalance: float = 60.0,
        daily_stop_loss: float = 25.0,
        max_combined_cost: float = 0.960,
        db=None,
    ):
        self.gamma = gamma                    # Inventory risk penalty factor
        self.max_imbalance = max_imbalance    # Max allowable unhedged position (60 shares)
        self.daily_stop_loss = daily_stop_loss # Max allowable daily drawdown ($25.00)
        self.max_combined_cost = max_combined_cost # Strict cost ceiling for complete-set merging
        self.db = db                          # SQLite persistence manager

        self.up = Position()
        self.down = Position()

        # Cumulative PnL tracking
        self.total_complete_sets_merged: float = 0.0
        self.realized_arbitrage_pnl: float = 0.0
        self.total_fees_paid: float = 0.0
        self.is_stop_loss_triggered: bool = False

        self.session_id: str = "GLOBAL"
        self.allocated_capital: float = 300.0

        if self.db is not None:
            self.load_persisted_state()

    def settle_contract_round(self, winning_side: Optional[str] = None, market_title: str = "") -> float:
        """
        Settles any residual unmerged tokens when a 5M/15M contract round concludes/resolves,
        books the final round payout into realized PnL, and resets active positions to 0.0
        for the fresh upcoming contract round.
        """
        up_shares = self.up.shares
        up_spent = self.up.total_spent
        down_shares = self.down.shares
        down_spent = self.down.total_spent

        settlement_pnl = 0.0

        if winning_side:
            winning_side = winning_side.upper()
            if winning_side == "UP":
                # UP pays $1.00 per share, DOWN expires at $0.00
                payout = up_shares * 1.00
                settlement_pnl = payout - (up_spent + down_spent)
            elif winning_side == "DOWN":
                # DOWN pays $1.00 per share, UP expires at $0.00
                payout = down_shares * 1.00
                settlement_pnl = payout - (up_spent + down_spent)
            else:
                settlement_pnl = 0.0
        else:
            # If no clear oracle outcome, redeem winning side based on highest share inventory
            settlement_pnl = 0.0

        self.realized_arbitrage_pnl += settlement_pnl

        logger.info(
            f"🏁 [CONTRACT SETTLED & REDEEMED] '{market_title}' | Residual: {up_shares:.1f} UP, {down_shares:.1f} DOWN | "
            f"Winner: {winning_side or 'N/A'} | Settlement PnL: {settlement_pnl:+.2f} | "
            f"Total Cumulative PnL: ${self.realized_arbitrage_pnl:.2f}. Resetting active inventory to 0.0 for new round."
        )

        # Reset active positions for the fresh round
        self.up = Position()
        self.down = Position()

        if self.db:
            self.db.save_position("UP", 0.0, 0.0, 0.0)
            self.db.save_position("DOWN", 0.0, 0.0, 0.0)

        return settlement_pnl

    def reset_for_session(self, session_id: str, allocated_capital: float = 300.0):
        """Resets active inventory and session metrics for a brand new isolated session."""
        self.session_id = session_id
        self.allocated_capital = max(10.0, min(300.0, float(allocated_capital)))
        self.up = Position()
        self.down = Position()
        self.total_complete_sets_merged = 0.0
        self.realized_arbitrage_pnl = 0.0
        self.total_fees_paid = 0.0
        self.is_stop_loss_triggered = False
        if self.db:
            self.db.save_position("UP", 0.0, 0.0, 0.0)
            self.db.save_position("DOWN", 0.0, 0.0, 0.0)
        logger.info(f"[SESSION INITIALIZED] New session {session_id} started with ${self.allocated_capital:.2f} capital.")

    def load_persisted_state(self):
        """Restores positions, cumulative PnL, and merged set counts from SQLite database."""
        if not self.db:
            return
        try:
            # Check if active session exists in DB
            active_sess = self.db.get_active_session()
            if active_sess:
                self.session_id = active_sess["session_id"]
                self.allocated_capital = active_sess.get("allocated_capital", 300.0)
                # Load stats specifically for this active session
                analytics = self.db.get_session_analytics(self.session_id)
                self.total_complete_sets_merged = analytics.get("total_complete_sets_merged", 0.0)
                self.realized_arbitrage_pnl = analytics.get("realized_arbitrage_pnl", 0.0)
                self.total_fees_paid = analytics.get("total_fees_paid", 0.0)
            else:
                analytics = self.db.get_analytics()
                self.total_complete_sets_merged = analytics.get("total_complete_sets_merged", 0.0)
                self.realized_arbitrage_pnl = analytics.get("realized_arbitrage_pnl", 0.0)
                self.total_fees_paid = analytics.get("total_fees_paid", 0.0)

            positions = self.db.load_positions()
            up_data = positions.get("UP", {})
            down_data = positions.get("DOWN", {})

            self.up.shares = up_data.get("shares", 0.0)
            self.up.avg_cost = up_data.get("avg_cost", 0.0)
            self.up.total_spent = up_data.get("total_spent", 0.0)

            self.down.shares = down_data.get("shares", 0.0)
            self.down.avg_cost = down_data.get("avg_cost", 0.0)
            self.down.total_spent = down_data.get("total_spent", 0.0)

            logger.info(
                f"[DATABASE RESTORED] Session: {self.session_id} | UP: {self.up.shares} shs (@${self.up.avg_cost:.3f}) | "
                f"DOWN: {self.down.shares} shs (@${self.down.avg_cost:.3f}) | "
                f"Merged Sets: {self.total_complete_sets_merged} | PnL: +${self.realized_arbitrage_pnl:.2f}"
            )
        except Exception as e:
            logger.error(f"Error restoring inventory state from database: {e}")

    def on_fill(self, side: str, price: float, shares: float, fee: float = 0.0):
        """Processes an executed limit order fill and persists position updates."""
        self.total_fees_paid += fee
        side_upper = side.upper()

        if side_upper == "UP":
            self.up.add_fill(shares, price)
            if self.db:
                self.db.save_position("UP", self.up.shares, self.up.avg_cost, self.up.total_spent)
        elif side_upper == "DOWN":
            self.down.add_fill(shares, price)
            if self.db:
                self.db.save_position("DOWN", self.down.shares, self.down.avg_cost, self.down.total_spent)

        # Check if complete sets can be formed and merged
        self._check_and_merge_complete_sets()
        self._check_stop_loss()

    def _check_and_merge_complete_sets(self):
        """
        Merges matching UP and DOWN tokens into complete sets (1 UP + 1 DOWN = $1.00 USDC).
        Strict Invariant: ONLY merges if combined cost per set <= max_combined_cost (guaranteeing profit).
        """
        matchable_sets = min(self.up.shares, self.down.shares)

        if matchable_sets >= 1.0:
            combined_cost_per_set = self.up.avg_cost + self.down.avg_cost

            # Strict guard: Never merge at a loss!
            if combined_cost_per_set > self.max_combined_cost:
                logger.debug(
                    f"[MERGE DEFERRED] {matchable_sets:.1f} candidate sets @ combined cost ${combined_cost_per_set:.3f} "
                    f"exceeds max allowable cost ceiling ${self.max_combined_cost:.3f}. Holding for better VWAP."
                )
                return

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

            # Persist positions and ledger event
            if self.db:
                self.db.save_position("UP", self.up.shares, self.up.avg_cost, self.up.total_spent)
                self.db.save_position("DOWN", self.down.shares, self.down.avg_cost, self.down.total_spent)
                self.db.log_complete_set(
                    sets_merged=matchable_sets,
                    up_avg_cost=self.up.avg_cost,
                    down_avg_cost=self.down.avg_cost,
                    combined_cost=combined_cost_per_set,
                    profit_locked=total_profit_locked,
                    cumulative_pnl=self.realized_arbitrage_pnl,
                    session_id=self.session_id,
                )

    # Backward compatibility alias
    record_fill = on_fill

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
            "total_spent": round(self.up.total_spent + self.down.total_spent, 2),
            "complete_sets_merged": round(self.total_complete_sets_merged, 1),
            "realized_arb_pnl": round(self.realized_arbitrage_pnl, 2),
            "fees_paid": round(self.total_fees_paid, 2),
            "net_pnl": round(net_pnl, 2),
            "is_stop_loss_triggered": self.is_stop_loss_triggered,
        }
