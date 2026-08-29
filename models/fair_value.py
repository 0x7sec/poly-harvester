"""
Fair Implied Probability Model using Bayesian spot momentum and logistic mapping.
"""
import math
import numpy as np


class FairValueModel:
    """
    Estimates the fair implied probability q_UP and q_DOWN of a short-term binary contract
    based on real-time spot price velocity from KuCoin.
    """

    def __init__(self, momentum_sensitivity: float = 2.5, base_prior: float = 0.50):
        self.momentum_sensitivity = momentum_sensitivity
        self.base_prior = base_prior

    def calculate_fair_probabilities(
        self,
        spot_velocity: float,
        spot_percent_return: float,
        polymarket_up_mid: float = 0.50,
    ) -> dict:
        """
        Calculates fair probabilities q_UP and q_DOWN.

        spot_velocity: Rate of spot price change (USD/second)
        spot_percent_return: Fractional price change over the lookback window (e.g. +0.0015 for +0.15%)
        polymarket_up_mid: Current Polymarket implied probability (midpoint)
        """
        # Convert percent return into a standardized momentum z-score
        # 0.10% move in 10s is a strong signal for 5-minute markets
        scaled_momentum = (spot_percent_return * 1000.0) * self.momentum_sensitivity

        # Logistic (Sigmoid) Bayesian update around Polymarket's current baseline
        # P(UP | Momentum) = 1 / (1 + exp(-z))
        # Where z incorporates prior odds + log likelihood ratio from momentum
        prior_logit = math.log(polymarket_up_mid / max(0.01, (1.0 - polymarket_up_mid)))
        posterior_logit = prior_logit + scaled_momentum

        # Clip logit to prevent extreme overflow
        posterior_logit = max(-4.0, min(4.0, posterior_logit))

        q_up = 1.0 / (1.0 + math.exp(-posterior_logit))
        # Keep within reasonable bounds [0.05, 0.95]
        q_up = max(0.05, min(0.95, round(q_up, 4)))
        q_down = round(1.0 - q_up, 4)

        return {
            "q_up": q_up,
            "q_down": q_down,
            "momentum_score": round(scaled_momentum, 3),
            "spot_velocity": round(spot_velocity, 2),
            "spot_pct_return": round(spot_percent_return * 100.0, 4),
        }
