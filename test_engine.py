"""
Unit and Integration verification tests for the Polymarket Quant Engine components,
including Binance integration and Mandatory Risk Safeguards.
"""
import unittest
from models.fair_value import FairValueModel
from models.inventory import InventoryManager
from models.quoter import QuotingEngine
from execution.paper_engine import PaperTradingEngine


class TestPolymarketQuantEngine(unittest.TestCase):

    def test_complete_set_accumulation_and_merging(self):
        """Tests that buying UP at $0.44 and DOWN at $0.51 merges into 1 Complete Set with $0.05 profit."""
        inventory = InventoryManager()

        # Step 1: Buy 100 UP @ 0.44
        inventory.on_fill("UP", price=0.44, shares=100.0)
        self.assertEqual(inventory.up.shares, 100.0)
        self.assertEqual(inventory.down.shares, 0.0)
        self.assertEqual(inventory.total_complete_sets_merged, 0.0)

        # Step 2: Buy 100 DOWN @ 0.51
        inventory.on_fill("DOWN", price=0.51, shares=100.0)

        # Step 3: Verify complete sets merged
        # Combined cost = $0.44 + $0.51 = $0.95. Profit per set = $0.05. Total profit = 100 * 0.05 = $5.00
        self.assertEqual(inventory.total_complete_sets_merged, 100.0)
        self.assertAlmostEqual(inventory.realized_arbitrage_pnl, 5.00, places=2)
        self.assertEqual(inventory.up.shares, 0.0)
        self.assertEqual(inventory.down.shares, 0.0)

    def test_hard_complete_set_cost_ceiling(self):
        """Tests Rule: Never buy both sides for more than $0.960."""
        quoter = QuotingEngine(
            target_edge=0.040,
            max_combined_cost=0.960,
            min_bid_price=0.05,
            max_bid_price=0.95,
        )

        quotes = quoter.calculate_quotes(
            q_up=0.60,
            q_down=0.40,
            stoikov_skew=0.0,
            net_imbalance=0.0,
        )

        # Sum must be <= 0.960
        self.assertLessEqual(quotes["quote_up"] + quotes["quote_down"], 0.960)
        self.assertGreaterEqual(quotes["projected_edge"], 0.040)

    def test_strict_inventory_cap_enforcement(self):
        """Tests Rule: Never hold more than 100 unhedged shares on one side."""
        quoter = QuotingEngine(
            target_edge=0.040,
            max_combined_cost=0.960,
            max_imbalance=100.0,
        )

        # Holding 100 UP shares -> Must freeze quoting new UP bids
        quotes = quoter.calculate_quotes(
            q_up=0.50,
            q_down=0.50,
            stoikov_skew=0.03,
            net_imbalance=100.0,
            up_avg_cost=0.46,
        )

        self.assertFalse(quotes["allow_quote_up"], "Should freeze UP bids when inventory cap is reached")
        self.assertTrue(quotes["allow_quote_down"], "Should continue allowing DOWN bids to complete sets")

    def test_daily_stop_loss_circuit_breaker(self):
        """Tests Rule: Shuts down engine if daily loss reaches -$30.00."""
        inventory = InventoryManager(daily_stop_loss=30.0)

        # Simulate fee drag or trade losses
        inventory.on_fill("UP", price=0.50, shares=10.0, fee=35.0)

        self.assertTrue(inventory.is_stop_loss_triggered, "Stop-loss must trigger when net PnL <= -$30.00")
        summary = inventory.get_summary()
        self.assertTrue(summary["is_stop_loss_triggered"])

    def test_paper_trading_fill_simulation(self):
        """Tests paper trading engine fills virtual limit bids when market ask touches limit price."""
        inventory = InventoryManager()
        engine = PaperTradingEngine(inventory=inventory, order_size_shares=25.0)

        engine.update_quotes(quote_up=0.45, quote_down=0.48, allow_up=True, allow_down=True)

        fills = engine.check_fills(
            up_market_ask=0.45,
            up_last_trade=0.46,
            down_market_ask=0.52,
            down_last_trade=0.53,
        )

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["side"], "UP")
        self.assertEqual(fills[0]["price"], 0.45)
        self.assertEqual(inventory.up.shares, 25.0)


if __name__ == "__main__":
    unittest.main()
