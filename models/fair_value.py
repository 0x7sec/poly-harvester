"""
Fair Implied Probability Model using a multi-factor Bayesian logistic mapping.

Factors (matching the Polkadot-Frog HFT profile):
  1. Spot momentum (dPrice/dt) — the primary directional signal.
  2. Acceleration (second derivative of price) — confirms or fades the move.
  3. Volatility normalization — a move is weighted by how "normal" it is
     relative to recent realized volatility (z-score), so the model does not
     over-react to routine noise or under-react to genuine dislocations.
  4. Order-book depth imbalance — the Polymarket book's bid/ask depth on each
     leg is a direct read on where the market participants are stacking.
  5. Time-to-expiry decay — the same momentum is more decisive as the contract
     approaches resolution (less time to reverse).

The output is a calibrated posterior probability q_UP in [0.05, 0.95].
"""
import math
from typing import Optional


class FairValueModel:
    """
    Estimates the fair implied probability q_UP and q_DOWN of a short-term
    binary contract from multi-factor real-time signals.
    """

    def __init__(
        self,
        momentum_sensitivity: float = 1.0,
        base_prior: float = 0.50,
        vol_lookback: int = 60,
        min_vol: float = 1e-6,
        accel_weight: float = 0.5,
        book_depth_weight: float = 0.5,
        time_decay_weight: float = 0.5,
        contract_duration: float = 300.0,
    ):
        self.momentum_sensitivity = momentum_sensitivity
        self.base_prior = base_prior
        self.vol_lookback = vol_lookback
        self.min_vol = min_vol
        self.accel_weight = accel_weight
        self.book_depth_weight = book_depth_weight
        self.time_decay_weight = time_decay_weight
        self.contract_duration = contract_duration

        # Rolling window of (timestamp, price) for realized-vol estimation.
        self._price_window = []

    def observe(self, price: float, ts: Optional[float] = None) -> None:
        """Feed a spot price tick into the rolling volatility window."""
        import time as _time
        if ts is None:
            ts = _time.time()
        self._price_window.append((ts, price))
        # Keep the window bounded.
        cutoff = ts - self.vol_lookback
        while self._price_window and self._price_window[0][0] < cutoff:
            self._price_window.pop(0)

    def _realized_vol_per_sec(self, now: Optional[float] = None) -> float:
        """Standard deviation of per-second log returns over the lookback window."""
        import time as _time
        if now is None:
            now = _time.time()
        if len(self._price_window) < 3:
            return 0.0
        # Use only the most recent vol_lookback seconds.
        cutoff = now - self.vol_lookback
        pts = [(t, p) for (t, p) in self._price_window if t >= cutoff]
        if len(pts) < 3:
            return 0.0
        rets = []
        for i in range(1, len(pts)):
            t0, p0 = pts[i - 1]
            t1, p1 = pts[i]
            dt = max(1e-3, t1 - t0)
            if p0 > 0:
                # per-second log return
                rets.append(math.log(p1 / p0) / dt)
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(max(0.0, var))

    def _acceleration(self, price: float, ts: Optional[float] = None) -> float:
        """
        Second derivative of price (USD/sec^2) over a short recent window.
        Positive = the move is accelerating; negative = decelerating/reversing.
        """
        import time as _time
        if ts is None:
            ts = _time.time()
        # Need a short recent window (~5s) to estimate the derivative of velocity.
        cutoff = ts - 5.0
        pts = [(t, p) for (t, p) in self._price_window if t >= cutoff]
        if len(pts) < 3:
            return 0.0
        # Velocity at the start vs the end of the window.
        def _vel(sub):
            if len(sub) < 2:
                return 0.0
            dt = max(1e-3, sub[-1][0] - sub[0][0])
            return (sub[-1][1] - sub[0][1]) / dt
        # Split the window in half.
        mid = len(pts) // 2
        v_early = _vel(pts[: mid + 1])
        v_late = _vel(pts[mid:])
        dt = max(1e-3, pts[-1][0] - pts[0][0])
        return (v_late - v_early) / dt

    def calculate_fair_probabilities(
        self,
        spot_velocity: float,
        spot_percent_return: float,
        polymarket_up_mid: float = 0.50,
        up_bid_depth: float = 0.0,
        down_bid_depth: float = 0.0,
        up_ask_depth: float = 0.0,
        down_ask_depth: float = 0.0,
        seconds_to_expiry: Optional[float] = None,
        now: Optional[float] = None,
    ) -> dict:
        """
        Calculates fair probabilities q_UP and q_DOWN.

        spot_velocity: Rate of spot price change (USD/second).
        spot_percent_return: Fractional price change over the lookback window.
        polymarket_up_mid: Current Polymarket implied probability (midpoint).
        up_bid_depth / down_bid_depth: Total resting bid size (shares) on each leg.
        up_ask_depth / down_ask_depth: Total resting ask size (shares) on each leg.
        seconds_to_expiry: Time remaining in the contract (for time-decay weighting).
        """
        import time as _time
        if now is None:
            now = _time.time()

        # --- Factor 1+2: volatility-normalized momentum + acceleration ---
        vol = self._realized_vol_per_sec(now)
        # z-score of the recent move: how many "normal" seconds of vol is this?
        if vol > self.min_vol:
            # Convert percent return into a per-second-equivalent and z-score it.
            move_z = (spot_percent_return * 100.0) / (vol * 100.0 * 10.0)
        else:
            move_z = 0.0
        move_z = max(-3.0, min(3.0, move_z))

        accel = self._acceleration(None, now)
        # Normalize acceleration against vol too (USD/sec^2 vs vol USD/sec).
        accel_z = (accel / (vol * 10.0)) if vol > self.min_vol else 0.0
        accel_z = max(-2.0, min(2.0, accel_z))

        momentum_term = self.momentum_sensitivity * (move_z + self.accel_weight * accel_z)

        # --- Factor 3: order-book depth imbalance ---
        up_supply = up_bid_depth + up_ask_depth
        down_supply = down_bid_depth + down_ask_depth
        total_supply = up_supply + down_supply
        if total_supply > 0:
            # A heavier UP book (more UP resting liquidity) suggests UP is the
            # "rich" side; push probability toward the side with more bid demand.
            up_bid_share = up_bid_depth / max(1e-6, up_bid_depth + down_bid_depth)
            book_imbalance = (up_bid_share - 0.5) * 2.0  # in [-1, 1]
            book_term = self.book_depth_weight * book_imbalance
        else:
            book_term = 0.0

        # --- Factor 4: time-to-expiry decay ---
        # As expiry approaches, the current momentum is more decisive.
        if seconds_to_expiry is not None and self.contract_duration > 0:
            frac_remaining = max(0.0, min(1.0, seconds_to_expiry / self.contract_duration))
            # Weight ramps from ~0.5 (far from expiry) to 1.0 (at expiry).
            time_weight = 1.0 - 0.5 * frac_remaining
        else:
            time_weight = 1.0
        momentum_term *= (1.0 + self.time_decay_weight * (time_weight - 0.75))

        # --- Bayesian logistic update around the Polymarket baseline ---
        prior_logit = math.log(max(0.01, polymarket_up_mid) / max(0.01, 1.0 - polymarket_up_mid))
        posterior_logit = prior_logit + momentum_term + book_term
        posterior_logit = max(-4.0, min(4.0, posterior_logit))

        q_up = 1.0 / (1.0 + math.exp(-posterior_logit))
        q_up = max(0.05, min(0.95, round(q_up, 4)))
        q_down = round(1.0 - q_up, 4)

        return {
            "q_up": q_up,
            "q_down": q_down,
            "momentum_score": round(momentum_term, 4),
            "move_z": round(move_z, 4),
            "accel_z": round(accel_z, 4),
            "realized_vol_per_sec": round(vol, 6),
            "book_term": round(book_term, 4),
            "time_weight": round(time_weight, 4),
            "spot_velocity": round(spot_velocity, 2),
            "spot_pct_return": round(spot_percent_return * 100.0, 4),
        }
