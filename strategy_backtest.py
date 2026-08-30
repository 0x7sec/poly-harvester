"""
Backtest Harness for the Poly-Harvester complete-set + directional-residual strategy.

Replays historical BTC spot price ticks (or a synthetic random-walk if no data is
supplied) against the fair-value model and a simplified version of the quoting /
complete-set / inventory logic, and reports whether the strategy's edge is real.

What it measures:
  1. Complete-set arbitrage: buying UP+DOWN when their combined cost < $1.00.
  2. Directional residual: the P&L from holding the leg the fair-value model
     favors, settled at the true resolution outcome.
  3. Model calibration: how well the model's q_up predicts the actual UP win
     rate (Brier score + accuracy), which tells you if the momentum signal is
     genuinely predictive.

Data source:
  - Pass `ticks` as a list of (timestamp, price) tuples, OR
  - Pass `data_path` to a CSV with `timestamp,price` columns, OR
  - Pass nothing to generate a synthetic random walk (useful for smoke tests).

Resolution model:
  For each N-second "contract window" (default 300s = 5m), the outcome is UP if
  the price at window-end >= price at window-start, else DOWN. This matches
  Polymarket's 5m/15m "Up or Down" contracts.
"""
from __future__ import annotations

import csv
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from models.fair_value import FairValueModel


@dataclass
class BacktestResult:
    total_windows: int = 0
    up_wins: int = 0
    down_wins: int = 0
    # Complete-set arbitrage
    complete_sets_formed: float = 0.0
    arb_profit: float = 0.0
    # Directional residual
    directional_trades: int = 0
    directional_profit: float = 0.0
    directional_wins: int = 0
    # Model calibration
    brier_score: float = 0.0
    model_accuracy: float = 0.0
    # Totals
    total_pnl: float = 0.0
    max_drawdown_usd: float = 0.0
    per_window: List[dict] = field(default_factory=list)

    def summary(self) -> dict:
        acc = self.model_accuracy
        return {
            "total_windows": self.total_windows,
            "up_wins": self.up_wins,
            "down_wins": self.down_wins,
            "complete_sets_formed": round(self.complete_sets_formed, 2),
            "arb_profit": round(self.arb_profit, 4),
            "directional_trades": self.directional_trades,
            "directional_profit": round(self.directional_profit, 4),
            "directional_win_rate": round(
                self.directional_wins / self.directional_trades, 4
            ) if self.directional_trades else 0.0,
            "brier_score": round(self.brier_score, 4),
            "model_accuracy": round(acc, 4),
            "total_pnl": round(self.total_pnl, 4),
            "max_drawdown_usd": round(self.max_drawdown_usd, 4),
            "verdict": self._verdict(),
        }

    def _verdict(self) -> str:
        if self.total_windows == 0:
            return "NO DATA"
        parts = []
        if self.arb_profit > 0:
            parts.append(f"complete-set arb is profitable (+${self.arb_profit:.2f})")
        else:
            parts.append(f"complete-set arb lost (${self.arb_profit:.2f})")
        if self.directional_trades:
            parts.append(
                f"directional residual {self.directional_win_rate_str()} "
                f"(${self.directional_profit:+.2f})"
            )
        # Brier: 0.25 is a coin-flip baseline (always 0.5). Lower is better.
        if self.brier_score < 0.25:
            parts.append(f"model is calibrated/predictive (Brier {self.brier_score:.3f} < 0.25 baseline)")
        else:
            parts.append(f"model is NOT better than a coin flip (Brier {self.brier_score:.3f} >= 0.25)")
        return " | ".join(parts)

    def directional_win_rate_str(self) -> str:
        if not self.directional_trades:
            return "n/a"
        return f"{self.directional_wins}/{self.directional_trades} wins"


def load_ticks_from_csv(data_path: str) -> List[Tuple[float, float]]:
    """Loads (timestamp, price) ticks from a CSV with timestamp,price columns."""
    ticks = []
    with open(data_path, "r", newline="") as f:
        reader = csv.reader(f)
        header = None
        for row in reader:
            if not row:
                continue
            if header is None:
                header = [c.strip().lower() for c in row]
                if "timestamp" not in header or "price" not in header:
                    # Assume first two columns are timestamp, price.
                    ts_idx, price_idx = 0, 1
                else:
                    ts_idx = header.index("timestamp")
                    price_idx = header.index("price")
                continue
            try:
                ts = float(row[ts_idx])
                price = float(row[price_idx])
            except (ValueError, IndexError):
                continue
            ticks.append((ts, price))
    ticks.sort(key=lambda t: t[0])
    return ticks


def generate_synthetic_ticks(
    duration_seconds: int = 3600,
    interval_seconds: float = 1.0,
    start_price: float = 78000.0,
    vol_per_sec: float = 0.0004,
    seed: Optional[int] = 42,
) -> List[Tuple[float, float]]:
    """Generates a synthetic geometric random walk for smoke tests."""
    import random
    rng = random.Random(seed)
    ticks = []
    price = start_price
    t0 = 1_000_000.0
    n = int(duration_seconds / interval_seconds)
    for i in range(n):
        ts = t0 + i * interval_seconds
        # Geometric Brownian motion step.
        drift = 0.0
        shock = rng.gauss(drift, vol_per_sec)
        price = max(1.0, price * math.exp(shock))
        ticks.append((ts, price))
    return ticks


class Backtester:
    """
    Replays ticks through the fair-value model and a simplified strategy to
    measure the complete-set arbitrage and directional-residual edge.
    """

    def __init__(
        self,
        fair_value_model: Optional[FairValueModel] = None,
        contract_window: int = 300,
        order_size_shares: float = 25.0,
        max_combined_cost: float = 0.96,
        min_edge: float = 0.01,
        fee_bps: float = 0.0,
        # Synthetic Polymarket book model: the market's quoted prices for UP/DOWN
        # are a noisy function of the "true" fair probability derived from the
        # realized drift over the window. We model the book as quoting the
        # prior (0.5) plus a fraction of the in-window momentum, plus noise.
        book_noise: float = 0.03,
    ):
        self.fvm = fair_value_model or FairValueModel()
        self.contract_window = contract_window
        self.order_size_shares = order_size_shares
        self.max_combined_cost = max_combined_cost
        self.min_edge = min_edge
        self.fee_bps = fee_bps
        self.book_noise = book_noise

    def _resolve_window(self, ticks_in_window: List[Tuple[float, float]]) -> Optional[str]:
        """Returns 'UP' or 'DOWN' for a contract window, or None if insufficient data."""
        if len(ticks_in_window) < 2:
            return None
        start_price = ticks_in_window[0][1]
        end_price = ticks_in_window[-1][1]
        if end_price >= start_price:
            return "UP"
        return "DOWN"

    def _simulate_book_quotes(
        self,
        true_fair_up: float,
        in_window_momentum: float,
        noise_rng,
    ) -> Tuple[float, float]:
        """
        Models the Polymarket book's quoted prices for UP and DOWN.

        The market quotes a probability that is a (noisy) blend of the prior
        (0.5) and the in-window momentum. This is the source of inefficiency the
        bot exploits: the book lags the true fair value.
        """
        # The book partially reflects momentum (market makers adjust), but with
        # noise and lag. true_fair_up is the "correct" probability.
        # We model the book's UP quote as true_fair_up + noise, clamped.
        noise = noise_rng.gauss(0.0, self.book_noise)
        book_up = max(0.05, min(0.95, true_fair_up + noise))
        book_down = max(0.05, min(0.95, 1.0 - book_up))
        return book_up, book_down

    def run(self, ticks: List[Tuple[float, float]]) -> BacktestResult:
        import random
        noise_rng = random.Random(1234)
        result = BacktestResult()
        if not ticks:
            return result

        # Split ticks into contract windows.
        window_ticks: List[List[Tuple[float, float]]] = []
        current_window_start = ticks[0][0]
        current: List[Tuple[float, float]] = []
        for ts, price in ticks:
            if ts - current_window_start >= self.contract_window:
                if current:
                    window_ticks.append(current)
                current = []
                current_window_start = ts
            current.append((ts, price))
        if current:
            window_ticks.append(current)

        # Pre-compute realized vol over the whole series for the model.
        for ts, price in ticks:
            self.fvm.observe(price, ts)

        peak_pnl = 0.0
        for w_idx, w in enumerate(window_ticks):
            outcome = self._resolve_window(w)
            if outcome is None:
                continue
            result.total_windows += 1
            if outcome == "UP":
                result.up_wins += 1
            else:
                result.down_wins += 1

            window_start_price = w[0][1]
            window_end_price = w[-1][1]
            # TRUE in-window return (used only to define the resolution outcome
            # and the book's "efficient" price — NOT for the model's decision).
            in_window_ret = (window_end_price - window_start_price) / window_start_price if window_start_price > 0 else 0.0

            # The "true" fair probability of UP, derived from the in-window
            # drift. A positive drift means UP is more likely. We map the
            # fractional return to a probability via a logistic.
            true_fair_up = 1.0 / (1.0 + math.exp(-max(-4, min(4, in_window_ret * 1000.0))))

            # --- NO-LOOKAHEAD model decision ---
            # A live model decides at a decision point (default: 50% through the
            # window) using ONLY data up to that point. We use the return from
            # the window start to the decision point as the momentum signal.
            mid_idx = max(1, len(w) // 2)
            decision_price = w[mid_idx][1]
            decision_ts = w[mid_idx][0]
            past_ret = (decision_price - window_start_price) / window_start_price if window_start_price > 0 else 0.0

            model_out = self.fvm.calculate_fair_probabilities(
                spot_velocity=0.0,
                spot_percent_return=past_ret,
                polymarket_up_mid=0.50,
                seconds_to_expiry=float(len(w) - mid_idx),  # time remaining after decision
                now=decision_ts,
            )
            q_up = model_out["q_up"]

            # Model calibration: Brier score contribution.
            outcome_bit = 1.0 if outcome == "UP" else 0.0
            result.brier_score += (q_up - outcome_bit) ** 2

            # Model accuracy: did the model pick the winning side?
            model_pick = "UP" if q_up >= 0.5 else "DOWN"
            if model_pick == outcome:
                result.model_accuracy += 1.0  # accumulate; divide later

            # --- Complete-set arbitrage simulation ---
            # The book quotes UP/DOWN with noise around the true fair value.
            book_up, book_down = self._simulate_book_quotes(true_fair_up, in_window_ret, noise_rng)
            combined_cost = book_up + book_down
            fee_per_share = (self.fee_bps / 10000.0) * combined_cost
            net_cost = combined_cost + fee_per_share
            if net_cost < 1.0 and (1.0 - net_cost) >= self.min_edge:
                # Buy the complete set; profit = 1.0 - net_cost per set.
                sets = self.order_size_shares
                profit = sets * (1.0 - net_cost)
                result.complete_sets_formed += sets
                result.arb_profit += profit

            # --- Directional residual simulation ---
            # The bot keeps a residual in the leg the model favors. If the model
            # picks the winner, it profits; otherwise it loses.
            residual_leg = model_pick
            residual_entry = book_up if residual_leg == "UP" else book_down
            if 0.05 <= residual_entry <= 0.95:
                result.directional_trades += 1
                # Entry fee (bps of notional) is paid when we take the residual.
                entry_fee = self.order_size_shares * residual_entry * (self.fee_bps / 10000.0)
                if residual_leg == outcome:
                    # Wins $1.00 per share at resolution.
                    pnl = self.order_size_shares * (1.0 - residual_entry) - entry_fee
                    result.directional_wins += 1
                else:
                    # Loses the entry cost.
                    pnl = -self.order_size_shares * residual_entry - entry_fee
                result.directional_profit += pnl

            # Track PnL and drawdown.
            window_pnl = (
                (result.arb_profit) + result.directional_profit
            )
            # Per-window incremental pnl for drawdown tracking.
            incremental = (
                (self.order_size_shares * (1.0 - net_cost))
                if (net_cost < 1.0 and (1.0 - net_cost) >= self.min_edge)
                else 0.0
            )
            if residual_leg:
                incremental += pnl if (0.05 <= residual_entry <= 0.95) else 0.0
            result.total_pnl = result.arb_profit + result.directional_profit
            peak_pnl = max(peak_pnl, result.total_pnl)
            result.max_drawdown_usd = max(
                result.max_drawdown_usd, peak_pnl - result.total_pnl
            )
            result.per_window.append({
                "window": w_idx,
                "outcome": outcome,
                "true_fair_up": round(true_fair_up, 4),
                "model_q_up": q_up,
                "model_pick": model_pick,
                "model_correct": model_pick == outcome,
                "book_up": round(book_up, 4),
                "book_down": round(book_down, 4),
                "combined_cost": round(combined_cost, 4),
                "arb_profit": round(incremental, 4),
            })

        if result.total_windows > 0:
            result.brier_score = result.brier_score / result.total_windows
            result.model_accuracy = result.model_accuracy / result.total_windows
        return result


def run_backtest(
    ticks: Optional[List[Tuple[float, float]]] = None,
    data_path: Optional[str] = None,
    contract_window: int = 300,
    order_size_shares: float = 25.0,
    max_combined_cost: float = 0.96,
    min_edge: float = 0.01,
    fee_bps: float = 0.0,
    synthetic_duration: int = 3600,
    seed: Optional[int] = 42,
) -> dict:
    """
    Convenience entry point. Returns the backtest summary dict.

    Priority for data source:
      1. `ticks` if provided
      2. `data_path` CSV if provided
      3. synthetic random walk (smoke test)
    """
    if ticks is None and data_path:
        ticks = load_ticks_from_csv(data_path)
    if ticks is None:
        ticks = generate_synthetic_ticks(duration_seconds=synthetic_duration, seed=seed)

    bt = Backtester(
        contract_window=contract_window,
        order_size_shares=order_size_shares,
        max_combined_cost=max_combined_cost,
        min_edge=min_edge,
        fee_bps=fee_bps,
    )
    result = bt.run(ticks)
    return result.summary()
