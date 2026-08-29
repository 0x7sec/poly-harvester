"""
Optimal Quoting Engine enforcing Complete-Set Cost Constraints and Inventory Skew.
"""
from typing import Dict, Tuple


class QuotingEngine:
    """
    Computes optimal limit bids for UP and DOWN tokens to guarantee positive expected edge
    and enforce complete set cost <= max_combined_cost.
    """

    def __init__(
        self,
        target_edge: float = 0.040,
        max_combined_cost: float = 0.960,
        min_bid_price: float = 0.05,
        max_bid_price: float = 0.95,
        max_imbalance: float = 75.0,
    ):
        self.target_edge = target_edge
        self.max_combined_cost = max_combined_cost
        self.min_bid_price = min_bid_price
        self.max_bid_price = max_bid_price
        self.max_imbalance = max_imbalance

    def calculate_quotes(
        self,
        q_up: float,
        q_down: float,
        stoikov_skew: float,
        net_imbalance: float,
        up_avg_cost: float = 0.0,
        down_avg_cost: float = 0.0,
        up_best_bid: float = 0.50,
        down_best_bid: float = 0.50,
    ) -> Dict[str, any]:
        """
        Calculates quote prices for UP and DOWN tokens, strictly guaranteeing
        that the complete-set cost (including already held inventory) never exceeds max_combined_cost.
        """
        half_edge = self.target_edge / 2.0

        # Step 1: Base quote with Stoikov inventory skew
        raw_quote_up = q_up - half_edge - stoikov_skew
        raw_quote_down = q_down - half_edge + stoikov_skew

        # Step 2: Invariant Check against existing inventory cost
        # If we already hold UP @ up_avg_cost, DOWN bid must strictly satisfy: Quote_DOWN <= MaxCost - up_avg_cost
        if net_imbalance > 0 and up_avg_cost > 0:
            max_allowed_down = max(0.01, self.max_combined_cost - up_avg_cost)
            raw_quote_down = min(raw_quote_down, max_allowed_down)
        elif net_imbalance < 0 and down_avg_cost > 0:
            max_allowed_up = max(0.01, self.max_combined_cost - down_avg_cost)
            raw_quote_up = min(raw_quote_up, max_allowed_up)

        # Step 3: Hard Complete-Set Cost constraint between the two quotes
        combined_cost = raw_quote_up + raw_quote_down
        if combined_cost > self.max_combined_cost:
            excess = (combined_cost - self.max_combined_cost) / 2.0
            raw_quote_up -= excess
            raw_quote_down -= excess

        # Step 4: Round to Polymarket tick size ($0.01)
        quote_up = round(max(self.min_bid_price, min(self.max_bid_price, raw_quote_up)), 2)
        quote_down = round(max(self.min_bid_price, min(self.max_bid_price, raw_quote_down)), 2)

        # Step 4b: Strict post-rounding inventory check
        if net_imbalance > 0 and up_avg_cost > 0:
            if (quote_down + up_avg_cost) > self.max_combined_cost:
                quote_down = round(max(0.01, self.max_combined_cost - up_avg_cost), 2)
                if (quote_down + up_avg_cost) > self.max_combined_cost:
                    quote_down = max(0.01, round(quote_down - 0.01, 2))
        elif net_imbalance < 0 and down_avg_cost > 0:
            if (quote_up + down_avg_cost) > self.max_combined_cost:
                quote_up = round(max(0.01, self.max_combined_cost - down_avg_cost), 2)
                if (quote_up + down_avg_cost) > self.max_combined_cost:
                    quote_up = max(0.01, round(quote_up - 0.01, 2))

        # Ensure post-rounding two-sided sum constraint holds
        if (quote_up + quote_down) > self.max_combined_cost:
            if quote_up >= quote_down:
                quote_up = round(quote_up - 0.01, 2)
            else:
                quote_down = round(quote_down - 0.01, 2)

        # Step 5: Risk circuit breaker - disable quoting the heavy side if imbalance is excessive
        allow_quote_up = True
        allow_quote_down = True

        if net_imbalance >= self.max_imbalance:
            # Overloaded on UP: Pause buying more UP, only buy DOWN to rebalance
            allow_quote_up = False
        elif net_imbalance <= -self.max_imbalance:
            # Overloaded on DOWN: Pause buying more DOWN, only buy UP to rebalance
            allow_quote_down = False

        projected_edge = round(1.00 - (quote_up + quote_down), 4)

        # Step 6: 0x50f7 Style Passive Tail Grid Quotes (1c - 2c convex asymmetry)
        tail_quote_up = 0.02 if q_up < 0.20 else 0.0
        tail_quote_down = 0.02 if q_down < 0.20 else 0.0

        return {
            "quote_up": quote_up,
            "quote_down": quote_down,
            "allow_quote_up": allow_quote_up,
            "allow_quote_down": allow_quote_down,
            "projected_cost": round(quote_up + quote_down, 3),
            "projected_edge": projected_edge,
            "applied_skew": round(stoikov_skew, 4),
            "tail_quote_up": tail_quote_up,
            "tail_quote_down": tail_quote_down,
        }
