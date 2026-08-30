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

    def test_live_engine_fill_reconciliation_real_orders(self):
        """Tests that live engine only records fills from real matched orders and not phantom public trades."""
        import asyncio
        from execution.live_engine import LiveTradingEngine
        from config import BotConfig

        async def _test():
            cfg = BotConfig(dry_run=False)
            inv = InventoryManager(max_combined_cost=0.960)
            engine = LiveTradingEngine(config=cfg, inventory=inv)

            # 1. Simulate active order
            engine.active_order_up = {
                "order_id": "0xreal_order_123",
                "side": "UP",
                "price": 0.45,
                "shares": 20.0,
                "filled_shares": 0.0,
                "token_id": "tok_up",
            }

            # 2. Mock get_order returning partial fill of 10 shares
            class MockOrder:
                id = "0xreal_order_123"
                price = 0.45
                size_matched = 10.0
                status = "LIVE"

            async def mock_get_order(ord_id):
                return MockOrder()

            engine.poly_manager.get_order = mock_get_order
            engine._last_fill_check_time = 0.0

            fills = await engine.check_fills()
            self.assertEqual(len(fills), 1)
            self.assertEqual(fills[0]["shares"], 10.0)
            self.assertEqual(inv.up.shares, 10.0)
            self.assertEqual(engine.active_order_up["filled_shares"], 10.0)

        asyncio.run(_test())

    def test_polymarket_order_rejection_detection(self):
        """Tests that RejectedOrder responses are correctly surfaced as ERROR status."""
        import asyncio
        from models.polymarket_client import PolymarketManager

        async def _test():
            mgr = PolymarketManager()

            # Mock secure client returning a RejectedOrder
            class MockRejectedOrder:
                ok = False
                code = 4001
                message = "insufficient collateral balance"

            class MockSecureClient:
                async def place_limit_order(self, **kwargs):
                    return MockRejectedOrder()

            mgr._secure_client = MockSecureClient()
            res = await mgr.place_limit_order(token_id="test_tok", side="BUY", price=0.45, amount_shares=10.0)
            self.assertEqual(res["status"], "ERROR")
        asyncio.run(_test())

    def test_dashboard_server_initialization_and_telemetry(self):
        """Tests that DashboardServer can be instantiated and generates valid telemetry."""
        import tempfile
        import shutil
        import os
        from config import BotConfig
        from main import PolymarketQuantEngine
        from dashboard.server import DashboardServer
        from storage.database import DatabaseManager

        temp_dir = tempfile.mkdtemp()
        try:
            db = DatabaseManager(db_path=os.path.join(temp_dir, "test.db"))
            config = BotConfig(enable_dashboard=False)
            engine = PolymarketQuantEngine(config, db=db)
            server = DashboardServer(engine=engine, host="127.0.0.1", port=8443)

            payload = server._get_telemetry_payload()
            self.assertIn("timestamp", payload)
            self.assertIn("session", payload)
            self.assertIn("binance", payload)
            self.assertIn("quotes", payload)
            self.assertIn("inventory", payload)
            self.assertEqual(payload["session"]["status"], "STANDBY")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_live_engine_quote_rate_limiting_cooldown(self):
        """Tests that live engine throttles quote cancellations/replacements to avoid spamming the CLOB."""
        import asyncio
        from config import BotConfig
        from execution.live_engine import LiveTradingEngine
        from models.inventory import InventoryManager

        async def _test():
            config = BotConfig(dry_run=False, order_size_shares=10.0)
            inventory = InventoryManager(max_combined_cost=0.960)
            engine = LiveTradingEngine(config=config, inventory=inventory)
            
            placed_orders = []
            cancelled_orders = []

            class MockPolyManager:
                async def ensure_secure_client(self):
                    return True
                def get_telemetry(self):
                    return {"geoblock": {"blocked": False}}
                async def place_limit_order(self, token_id, side, price, amount_shares):
                    ord_id = f"ord_{len(placed_orders)+1}"
                    placed_orders.append({"id": ord_id, "price": price, "side": side})
                    return {"status": "SUCCESS", "order_id": ord_id}
                async def cancel_order(self, order_id):
                    cancelled_orders.append(order_id)
                    return {"status": "SUCCESS"}

            engine.poly_manager = MockPolyManager()

            # 1. First quote -> Placed
            await engine.sync_orders(
                quote_up=0.45,
                quote_down=0.40,
                allow_up=True,
                allow_down=True,
                token_id_up="tok_up",
                token_id_down="tok_dn",
            )
            self.assertEqual(len(placed_orders), 2)
            self.assertEqual(placed_orders[0]["price"], 0.45)
            self.assertEqual(placed_orders[1]["price"], 0.40)

            # 2. Immediate micro-jitter (sub-cent: 0.452) -> Ignored
            await engine.sync_orders(
                quote_up=0.452,
                quote_down=0.40,
                allow_up=True,
                allow_down=True,
                token_id_up="tok_up",
                token_id_down="tok_dn",
            )
            self.assertEqual(len(placed_orders), 2, "Sub-cent quote jitter must not spam CLOB")

            # 3. Immediate 2-cent jump (0.47) within cooldown -> Ignored by cooldown
            await engine.sync_orders(
                quote_up=0.47,
                quote_down=0.40,
                allow_up=True,
                allow_down=True,
                token_id_up="tok_up",
                token_id_down="tok_dn",
            )
            self.assertEqual(len(placed_orders), 2, "Rapid tick changes within 0.5s cooldown must not spam CLOB")

            # 4. Simulate elapsed cooldown > 0.5s -> Now replaces order
            engine._last_quote_time_up = 0.0
            await engine.sync_orders(
                quote_up=0.47,
                quote_down=0.40,
                allow_up=True,
                allow_down=True,
                token_id_up="tok_up",
                token_id_down="tok_dn",
            )
            self.assertEqual(len(placed_orders), 3)
            self.assertEqual(placed_orders[-1]["price"], 0.47)
            self.assertEqual(len(cancelled_orders), 1)

        asyncio.run(_test())

    def test_live_engine_onchain_merge_failure_protection(self):
        """Tests that failed on-chain complete set merges do not corrupt inventory or cause ghost merges."""
        import asyncio
        from config import BotConfig
        from execution.live_engine import LiveTradingEngine
        from models.inventory import InventoryManager

        async def _test():
            config = BotConfig(dry_run=False)
            inventory = InventoryManager(max_combined_cost=0.960)
            engine = LiveTradingEngine(config=config, inventory=inventory)

            # Fill 20 UP @ 0.45 and 20 DOWN @ 0.45 (mergeable 20 sets @ 0.90) without auto-merge
            inventory.on_fill("UP", price=0.45, shares=20.0, auto_merge=False)
            inventory.on_fill("DOWN", price=0.45, shares=20.0, auto_merge=False)

            self.assertEqual(inventory.up.shares, 20.0)
            self.assertEqual(inventory.down.shares, 20.0)
            self.assertEqual(inventory.total_complete_sets_merged, 0.0)

            # Scenario A: On-chain transaction reverts / fails
            class FailingPolyManager:
                async def merge_complete_sets(self, condition_id, amount):
                    return {"status": "ERROR", "error": "execution reverted: gas limit exceeded"}

            engine.poly_manager = FailingPolyManager()
            
            class MockFeed:
                condition_id = "0xcond123"

            # Check fills / trigger merge
            engine._last_fill_check_time = 0.0
            await engine.check_fills(feed=MockFeed())

            # Invariant: Inventory balances must remain intact!
            self.assertEqual(inventory.up.shares, 20.0, "Inventory must NOT be deducted on failed on-chain merge")
            self.assertEqual(inventory.down.shares, 20.0, "Inventory must NOT be deducted on failed on-chain merge")
            self.assertEqual(inventory.total_complete_sets_merged, 0.0)
            self.assertEqual(inventory.realized_arbitrage_pnl, 0.0)

            # Scenario B: On-chain transaction succeeds
            class SuccessfulPolyManager:
                async def merge_complete_sets(self, condition_id, amount):
                    return {"status": "SUCCESS", "tx_handle": "0xtxhash456"}

            engine.poly_manager = SuccessfulPolyManager()
            engine._last_fill_check_time = 0.0
            await engine.check_fills(feed=MockFeed())

            # Now positions are cleanly merged
            self.assertEqual(inventory.up.shares, 0.0)
            self.assertEqual(inventory.down.shares, 0.0)
            self.assertEqual(inventory.total_complete_sets_merged, 20.0)
            self.assertAlmostEqual(inventory.realized_arbitrage_pnl, 2.00, places=2)

        asyncio.run(_test())

    def test_paper_engine_concurrent_fills_do_not_exceed_capital_ceiling(self):
        """Tests that concurrent UP and DOWN fills in paper mode strictly respect the allocated capital ceiling."""
        inventory = InventoryManager(max_combined_cost=0.960)
        inventory.allocated_capital = 10.00  # Strict $10.00 capital limit
        engine = PaperTradingEngine(inventory=inventory, order_size_shares=20.0, in_flight_latency_sec=0.0)

        # Place orders: UP 20 shs @ 0.45 ($9.00) and DOWN 20 shs @ 0.45 ($9.00) -> Total $18.00 > $10.00 limit
        engine.update_quotes(quote_up=0.45, quote_down=0.45, allow_up=True, allow_down=True)

        feed = PolymarketFeed(auto_discover=False)
        feed.up_best_ask = 0.45
        feed.up_asks = [{"price": 0.45, "size": 100.0}]
        feed.down_best_ask = 0.45
        feed.down_asks = [{"price": 0.45, "size": 100.0}]

        fills = engine.check_fills(
            up_market_ask=0.45,
            down_market_ask=0.45,
            feed=feed,
        )

        total_spent = inventory.up.total_spent + inventory.down.total_spent
        self.assertLessEqual(total_spent, 10.00, "Total paper trading spent capital must never exceed allocated budget")
        self.assertTrue(len(fills) >= 1)

    def test_orderbook_buy_fee_calculation(self):
        """Tests that simulate_buy_fill calculates fees proportionally to share volume and probability uncertainty."""
        from pm_trader.models import OrderBook, OrderBookLevel
        from pm_trader.orderbook import simulate_buy_fill, calculate_fee

        # 100 shares @ $0.50 with 100 bps fee rate (1%) -> Fee = 0.01 * min(0.5, 0.5) * 100 = $0.50
        manual_fee = calculate_fee(100, 0.50, 100.0)
        self.assertAlmostEqual(manual_fee, 0.50, places=4)

        book = OrderBook(
            bids=[],
            asks=[OrderBookLevel(price=0.50, size=100.0)],
        )
        res = simulate_buy_fill(book=book, amount_usd=50.0, fee_rate_bps=100, order_type="fak")
        self.assertEqual(res.total_shares, 100.0)
        self.assertAlmostEqual(res.fee, 0.50, places=4)


if __name__ == "__main__":
    unittest.main()
