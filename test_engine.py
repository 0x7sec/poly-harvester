"""
Unit and Integration verification tests for the Polymarket Quant Engine components,
including Binance integration, Realistic CLOB Matching Engine, and Mandatory Risk Safeguards.
"""
import time
import unittest
from models.fair_value import FairValueModel
from models.inventory import InventoryManager
from models.quoter import QuotingEngine
from execution.paper_engine import PaperTradingEngine, VirtualOrder
from feeds.polymarket_feed import PolymarketFeed


class TestPolymarketQuantEngine(unittest.TestCase):

    def test_complete_set_accumulation_and_merging(self):
        """Tests that buying UP at $0.44 and DOWN at $0.51 merges into 1 Complete Set with $0.05 profit."""
        inventory = InventoryManager(max_combined_cost=0.960)

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

    def test_no_negative_complete_set_merges(self):
        """Tests that buying UP at $0.55 and DOWN at $0.50 (cost $1.05 > $0.96) is NOT merged at a loss."""
        inventory = InventoryManager(max_combined_cost=0.960)

        inventory.on_fill("UP", price=0.55, shares=50.0)
        inventory.on_fill("DOWN", price=0.50, shares=50.0)

        # Must NOT merge because combined cost $1.05 exceeds max_combined_cost $0.96
        self.assertEqual(inventory.total_complete_sets_merged, 0.0)
        self.assertEqual(inventory.realized_arbitrage_pnl, 0.0)
        self.assertEqual(inventory.up.shares, 50.0)
        self.assertEqual(inventory.down.shares, 50.0)

    def test_hard_complete_set_cost_ceiling(self):
        """Tests Rule: Never buy both sides for more than $0.960."""
        quoter = QuotingEngine(
            target_edge=0.040,
            max_combined_cost=0.960,
            min_bid_price=0.05,
            max_bid_price=0.95,
            max_imbalance=60.0,
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
        """Tests Rule: Never hold more than max_imbalance unhedged shares on one side."""
        quoter = QuotingEngine(
            target_edge=0.040,
            max_combined_cost=0.960,
            max_imbalance=60.0,
        )

        # Holding 60 UP shares -> Must freeze quoting new UP bids
        quotes = quoter.calculate_quotes(
            q_up=0.50,
            q_down=0.50,
            stoikov_skew=0.03,
            net_imbalance=60.0,
            up_avg_cost=0.46,
        )

        self.assertFalse(quotes["allow_quote_up"], "Should freeze UP bids when inventory cap is reached")
        self.assertTrue(quotes["allow_quote_down"], "Should continue allowing DOWN bids to complete sets")

    def test_daily_stop_loss_circuit_breaker(self):
        """Tests Rule: Shuts down engine if daily loss reaches -$25.00."""
        inventory = InventoryManager(daily_stop_loss=25.0)

        # Simulate fee drag or trade losses
        inventory.on_fill("UP", price=0.50, shares=10.0, fee=30.0)

        self.assertTrue(inventory.is_stop_loss_triggered, "Stop-loss must trigger when net PnL <= -$25.00")
        summary = inventory.get_summary()
        self.assertTrue(summary["is_stop_loss_triggered"])

    def test_paper_engine_does_not_fill_on_dead_wide_market(self):
        """
        Tests that in a wide/dead market (e.g. 0.01 bid / 0.99 ask) with NO trades,
        bidding at $0.46 does NOT fill artificially.
        """
        inventory = InventoryManager(max_combined_cost=0.960)
        engine = PaperTradingEngine(inventory=inventory, order_size_shares=20.0, in_flight_latency_sec=0.0)

        feed = PolymarketFeed(auto_discover=False)
        feed.up_best_bid = 0.01
        feed.up_best_ask = 0.99
        feed.down_best_bid = 0.01
        feed.down_best_ask = 0.99
        feed.up_bids = [{"price": 0.01, "size": 100.0}]
        feed.up_asks = [{"price": 0.99, "size": 100.0}]

        engine.update_quotes(quote_up=0.46, quote_down=0.43, allow_up=True, allow_down=True, feed=feed)

        # No trade prints, ask is $0.99
        fills = engine.check_fills(
            up_market_ask=0.99,
            down_market_ask=0.99,
            feed=feed,
        )

        self.assertEqual(len(fills), 0, "Should NOT fill when spread is wide and no trade prints occur")
        self.assertEqual(inventory.up.shares, 0.0)
        self.assertEqual(inventory.down.shares, 0.0)

    def test_paper_engine_direct_cross_fill(self):
        """
        Tests direct fill when market ask crosses our limit bid price.
        """
        inventory = InventoryManager(max_combined_cost=0.960)
        engine = PaperTradingEngine(inventory=inventory, order_size_shares=20.0, in_flight_latency_sec=0.0)

        feed = PolymarketFeed(auto_discover=False)
        feed.up_best_ask = 0.45
        feed.up_asks = [{"price": 0.45, "size": 50.0}]

        engine.update_quotes(quote_up=0.46, quote_down=0.43, allow_up=True, allow_down=True, feed=feed)

        # Market ask is 0.45 <= our bid 0.46
        fills = engine.check_fills(
            up_market_ask=0.45,
            down_market_ask=0.55,
            feed=feed,
        )

        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["side"], "UP")
        self.assertEqual(fills[0]["shares"], 20.0)
        self.assertEqual(fills[0]["price"], 0.45)
        self.assertEqual(inventory.up.shares, 20.0)

    def test_paper_engine_queue_priority_and_trade_consumption(self):
        """
        Tests that when an order is placed behind 30 shares in queue,
        a trade of 20 shares consumes queue ahead and does NOT fill our order,
        but a subsequent trade of 20 shares fills 10 shares of our order (partial fill).
        """
        inventory = InventoryManager(max_combined_cost=0.960)
        engine = PaperTradingEngine(inventory=inventory, order_size_shares=20.0, in_flight_latency_sec=0.0)

        feed = PolymarketFeed(auto_discover=False)
        feed.up_bids = [{"price": 0.46, "size": 30.0}]
        feed.up_best_ask = 0.50

        # Place bid at 0.46; 30 shares are sitting ahead
        engine.update_quotes(quote_up=0.46, quote_down=0.43, allow_up=True, allow_down=True, feed=feed)
        self.assertEqual(engine.active_order_up.queue_ahead_shares, 30.0)

        # Trade 1: 20 shares @ 0.46 -> Consumes 20 shares of queue ahead (10 remaining ahead)
        feed.record_simulated_trade("UP", price=0.46, size=20.0)
        fills1 = engine.check_fills(up_market_ask=0.50, down_market_ask=0.50, feed=feed)
        self.assertEqual(len(fills1), 0, "Trade volume was absorbed by queue ahead; no fill yet")
        self.assertEqual(engine.active_order_up.queue_ahead_shares, 10.0)

        # Trade 2: 20 shares @ 0.46 -> Consumes remaining 10 shares ahead, fills 10 shares of our order
        feed.record_simulated_trade("UP", price=0.46, size=20.0)
        fills2 = engine.check_fills(up_market_ask=0.50, down_market_ask=0.50, feed=feed)
        self.assertEqual(len(fills2), 1)
        self.assertEqual(fills2[0]["shares"], 10.0)
        self.assertEqual(engine.active_order_up.remaining_shares, 10.0)
        self.assertEqual(inventory.up.shares, 10.0)


if __name__ == "__main__":
    unittest.main()
