"""
Unit and Integration Tests for SQLite DatabaseManager, User Auth, StateCache, and Inventory Recovery.
"""
import os
import shutil
import tempfile
import unittest

from storage.database import DatabaseManager
from storage.cache import StateCache
from models.inventory import InventoryManager
from execution.paper_engine import PaperTradingEngine


class TestStorageAndRecovery(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_poly.db")
        self.cache_dir = os.path.join(self.test_dir, "cache")
        self.db = DatabaseManager(db_path=self.db_path)
        self.cache = StateCache(cache_dir=self.cache_dir)

    def tearDown(self):
        self.cache.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_seeded_admin_user_authentication(self):
        """Tests that default admin is seeded and can authenticate successfully."""
        user = self.db.authenticate_user("admin", "polyharvester2026")
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "admin")
        self.assertEqual(user["role"], "admin")

        # Test invalid password
        invalid = self.db.authenticate_user("admin", "wrong_password")
        self.assertIsNone(invalid)

    def test_session_token_lifecycle(self):
        """Tests session creation, validation, and revocation."""
        token = self.db.create_auth_session(username="admin", role="admin", duration_seconds=3600)
        self.assertTrue(len(token) > 20)

        # Validate active session
        session = self.db.validate_session(token)
        self.assertIsNotNone(session)
        self.assertEqual(session["username"], "admin")

        # Revoke session
        self.db.revoke_session(token)
        revoked = self.db.validate_session(token)
        self.assertIsNone(revoked)

    def test_database_position_persistence(self):
        """Tests that saving positions to SQLite persists and can be loaded accurately."""
        self.db.save_position("UP", shares=40.0, avg_cost=0.45, total_spent=18.0)
        self.db.save_position("DOWN", shares=60.0, avg_cost=0.51, total_spent=30.6)

        loaded = self.db.load_positions()
        self.assertEqual(loaded["UP"]["shares"], 40.0)
        self.assertAlmostEqual(loaded["UP"]["avg_cost"], 0.45, places=2)
        self.assertEqual(loaded["DOWN"]["shares"], 60.0)
        self.assertAlmostEqual(loaded["DOWN"]["avg_cost"], 0.51, places=2)

    def test_complete_sets_ledger_persistence(self):
        """Tests complete set merge logging and analytics calculation."""
        self.db.log_complete_set(
            sets_merged=20.0,
            up_avg_cost=0.44,
            down_avg_cost=0.51,
            combined_cost=0.95,
            profit_locked=1.00,
            cumulative_pnl=1.00,
        )

        sets = self.db.get_complete_sets(limit=10)
        self.assertEqual(len(sets), 1)
        self.assertEqual(sets[0]["sets_merged"], 20.0)
        self.assertAlmostEqual(sets[0]["profit_locked"], 1.00, places=2)

        analytics = self.db.get_analytics()
        self.assertEqual(analytics["total_complete_sets_merged"], 20.0)
        self.assertAlmostEqual(analytics["realized_arbitrage_pnl"], 1.00, places=2)
        self.assertAlmostEqual(analytics["profit_margin_pct"], 5.0, places=1)

    def test_inventory_restart_recovery(self):
        """Tests that InventoryManager restores all holdings and PnL across process restarts."""
        # 1. First engine run: Accumulate 60 DOWN shares and 20 UP shares
        inv1 = InventoryManager(db=self.db, max_combined_cost=0.960)
        inv1.on_fill("DOWN", price=0.50, shares=60.0)
        inv1.on_fill("UP", price=0.44, shares=20.0)

        # 20 complete sets should be merged (cost = 0.44 + 0.50 = 0.94 <= 0.96)
        self.assertEqual(inv1.total_complete_sets_merged, 20.0)
        self.assertAlmostEqual(inv1.realized_arbitrage_pnl, 1.20, places=2) # 20 * 0.06 = 1.20
        self.assertEqual(inv1.up.shares, 0.0)
        self.assertEqual(inv1.down.shares, 40.0)

        # 2. Simulate process shutdown and restart with a fresh InventoryManager instance
        inv2 = InventoryManager(db=self.db, max_combined_cost=0.960)

        # Verify 100% state restoration
        self.assertEqual(inv2.up.shares, 0.0)
        self.assertEqual(inv2.down.shares, 40.0)
        self.assertAlmostEqual(inv2.down.avg_cost, 0.50, places=2)
        self.assertEqual(inv2.total_complete_sets_merged, 20.0)
        self.assertAlmostEqual(inv2.realized_arbitrage_pnl, 1.20, places=2)

    def test_state_cache_persistence(self):
        """Tests that Diskcache retains runtime config overrides."""
        config_override = {
            "order_size_shares": 25.0,
            "max_inventory_imbalance": 75.0,
            "max_combined_cost": 0.955,
            "daily_stop_loss_usd": 20.0,
        }
        self.cache.set_runtime_config(config_override)

        # Load back
        loaded = self.cache.get_runtime_config()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["order_size_shares"], 25.0)
        self.assertEqual(loaded["max_combined_cost"], 0.955)

    def test_paper_engine_trade_logging(self):
        """Tests that PaperTradingEngine records fills to SQLite."""
        inv = InventoryManager(db=self.db, max_combined_cost=0.960)
        engine = PaperTradingEngine(inventory=inv, order_size_shares=20.0, in_flight_latency_sec=0.0, db=self.db)

        engine.update_quotes(quote_up=0.46, quote_down=0.48, allow_up=True, allow_down=True)
        engine.check_fills(
            up_market_ask=0.46,
            up_last_trade=0.46,
            down_market_ask=0.55,
            down_last_trade=0.55,
        )

        trades = self.db.get_recent_trades(limit=10)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["side"], "UP")
        self.assertEqual(trades[0]["price"], 0.46)
        self.assertEqual(trades[0]["shares"], 20.0)

    def test_turnstile_config_persistence(self):
        """Tests that Cloudflare Turnstile configuration can be saved, updated, and retrieved."""
        # Initial default state
        cfg = self.db.get_turnstile_config()
        self.assertEqual(cfg["enabled"], 0)
        self.assertEqual(cfg["site_key"], "")
        self.assertEqual(cfg["secret_key"], "")

        # Enable Turnstile with keys
        saved = self.db.save_turnstile_config(
            enabled=True,
            site_key="0x4AAAAAA_test_site_key",
            secret_key="0x4AAAAAA_test_secret_key",
        )
        self.assertEqual(saved["enabled"], 1)
        self.assertEqual(saved["site_key"], "0x4AAAAAA_test_site_key")
        self.assertEqual(saved["secret_key"], "0x4AAAAAA_test_secret_key")

        # Disable Turnstile while preserving keys
        updated = self.db.save_turnstile_config(enabled=False)
        self.assertEqual(updated["enabled"], 0)
        self.assertEqual(updated["site_key"], "0x4AAAAAA_test_site_key")
        self.assertEqual(updated["secret_key"], "0x4AAAAAA_test_secret_key")

    def test_rapid_session_creation_no_collision(self):
        """Tests that rapid back-to-back session creation does not cause UNIQUE constraint collisions."""
        sess1 = self.db.create_session(name="Run 1", mode="PAPER")
        sess2 = self.db.create_session(name="Run 2", mode="PAPER")
        sess3 = self.db.create_session(name="Run 3", mode="PAPER")

        self.assertNotEqual(sess1["session_id"], sess2["session_id"])
        self.assertNotEqual(sess2["session_id"], sess3["session_id"])
        self.assertNotEqual(sess1["session_id"], sess3["session_id"])
        self.assertEqual(sess3["status"], "ACTIVE")

    def test_contract_rollover_settlement_logged_to_sqlite(self):
        """Tests that contract rollover settlement events are reliably logged to SQLite trades, complete_sets, and sessions."""
        sess = self.db.create_session(name="Test Rollover Session", mode="PAPER", allocated_capital=300.0)
        sess_id = sess["session_id"]

        inventory = InventoryManager(db=self.db, max_combined_cost=0.960)
        inventory.session_id = sess_id

        # 1. Fill 20 UP @ 0.45 ($9.00 spent)
        inventory.on_fill("UP", price=0.45, shares=20.0, fee=0.05, auto_merge=False)
        self.assertEqual(inventory.up.shares, 20.0)

        # 2. Settle round with UP winning (Payout = 20 * $1.00 = $20.00, PnL = $20.00 - $9.00 = +$11.00)
        settlement_pnl = inventory.settle_contract_round(winning_side="UP", market_title="BTC Rolling 5M", market_slug="btc-5m-123")
        self.assertAlmostEqual(settlement_pnl, 11.00, places=2)
        self.assertEqual(inventory.up.shares, 0.0)
        self.assertEqual(inventory.down.shares, 0.0)

        # 3. Verify SQLite trades table has SETTLEMENT entry
        trades = self.db.get_session_trades(session_id=sess_id)
        settle_trades = [t for t in trades if "SETTLE" in str(t.get("side", "")) or t.get("execution_type") == "SETTLEMENT"]
        self.assertTrue(len(settle_trades) >= 1)
        self.assertEqual(settle_trades[0]["market_slug"], "btc-5m-123")

        # 4. Verify complete_sets ledger has settlement row
        sets = self.db.get_complete_sets(limit=10)
        self.assertTrue(len(sets) >= 1)
        self.assertAlmostEqual(sets[0]["profit_locked"], 11.00, places=2)

        # 5. Verify trading session realized_pnl was updated in database
        updated_sess = self.db.get_session(sess_id)
        self.assertAlmostEqual(updated_sess["realized_pnl"], 11.00, places=2)


if __name__ == "__main__":
    unittest.main()
