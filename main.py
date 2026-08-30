"""
Main Event Loop & Live Dashboard for Polymarket Quant Engine using Binance Direct WebSocket Feed.
"""
import asyncio
import logging
import signal
import sys
import time
from typing import Any, Dict, List, Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.live import Live
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

from config import BotConfig
from feeds.binance_feed import BinanceFeed
from feeds.polymarket_feed import PolymarketFeed
from models.fair_value import FairValueModel
from models.inventory import InventoryManager
from models.quoter import QuotingEngine
from models.polymarket_client import PolymarketManager
from execution.paper_engine import PaperTradingEngine
from execution.live_engine import LiveTradingEngine
from backtest.recorder import TradeRecorder
from dashboard.server import DashboardServer
from storage.database import DatabaseManager
from storage.cache import StateCache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("bot_activity.log", encoding="utf-8")],
)
logger = logging.getLogger("PolymarketEngine")
console = Console() if HAVE_RICH else None


class PolymarketQuantEngine:
    def __init__(self, config: BotConfig, db: Optional[DatabaseManager] = None, cache: Optional[StateCache] = None):
        self.config = config

        # 1. Persistence & State Cache
        self.db = db or DatabaseManager()
        self.cache = cache or StateCache()

        # Restore cached runtime overrides if available
        cached_config = self.cache.get_runtime_config()
        if cached_config:
            if "order_size_shares" in cached_config:
                self.config.order_size_shares = float(cached_config["order_size_shares"])
            if "max_inventory_imbalance" in cached_config:
                self.config.max_inventory_imbalance = float(cached_config["max_inventory_imbalance"])
            if "max_combined_cost" in cached_config:
                self.config.max_combined_cost = float(cached_config["max_combined_cost"])
            if "daily_stop_loss_usd" in cached_config:
                self.config.daily_stop_loss_usd = float(cached_config["daily_stop_loss_usd"])
            logger.info(f"Restored runtime config overrides from Diskcache: {cached_config}")

        # Components with Mandatory Risk Rules & SQLite Persistence
        self.inventory = InventoryManager(
            gamma=config.inventory_risk_aversion,
            max_imbalance=config.max_inventory_imbalance,
            daily_stop_loss=config.daily_stop_loss_usd,
            max_combined_cost=config.max_combined_cost,
            db=self.db,
        )
        self.fair_value_model = FairValueModel(
            momentum_sensitivity=config.momentum_sensitivity
        )
        self.quoter = QuotingEngine(
            target_edge=config.target_edge_per_share,
            max_combined_cost=config.max_combined_cost,
            min_bid_price=config.min_bid_price,
            max_bid_price=config.max_bid_price,
            max_imbalance=config.max_inventory_imbalance,
        )
        self.paper_engine = PaperTradingEngine(
            inventory=self.inventory,
            order_size_shares=config.order_size_shares,
            db=self.db,
        )
        self.recorder = TradeRecorder(filepath=config.trade_log_file)

        # Polymarket Manager & Live Execution Engine
        poly_cfg = {}
        try:
            poly_cfg = self.db.get_polymarket_config()
        except Exception:
            pass

        pk = poly_cfg.get("private_key") or config.private_key
        wa = poly_cfg.get("wallet_address") or config.wallet_address
        prx = poly_cfg.get("proxy_url") or config.proxy_url

        self.poly_manager = PolymarketManager(
            private_key=pk,
            wallet_address=wa,
            proxy_url=prx,
        )
        self.live_engine = LiveTradingEngine(
            config=self.config,
            inventory=self.inventory,
            poly_manager=self.poly_manager,
        )

        # 1. Primary Reference Feed: Binance Direct High-Speed Stream
        self.binance_feed = BinanceFeed(
            symbol=config.binance_symbol,
            lookback_seconds=config.velocity_lookback_seconds,
            on_tick_callback=self._on_binance_tick,
        )

        # 2. Polymarket CLOB Feed
        self.polymarket_feed = PolymarketFeed(
            token_id_up=config.token_id_up,
            token_id_down=config.token_id_down,
            auto_discover=config.auto_discover_active_market,
            target_market_slug=config.target_market_slug,
            on_book_update_callback=self._on_polymarket_book,
            on_rollover_callback=self._on_contract_rollover,
        )

        # Strategy State
        self.current_quotes: dict = {}
        self.fair_prob: dict = {"q_up": 0.50, "q_down": 0.50, "momentum_score": 0.0}
        self.last_fill_event: str = "None"
        self._running: bool = False

        # Session & Trading Controls (Auto-active on startup)
        active_sess = self.db.get_active_session() if self.db else None
        if active_sess and active_sess.get("status") == "ACTIVE":
            self.current_session_id = active_sess["session_id"]
            self.is_trading_active = True
        else:
            self.current_session_id = f"SESS-{int(time.time())}"
            self.is_trading_active = True
            if self.db:
                try:
                    self.db.create_session(
                        name="Auto-Start Session",
                        mode="PAPER" if config.dry_run else "LIVE",
                        allocated_capital=300.0,
                        order_size_shares=config.order_size_shares,
                        notes="Auto-created on engine startup",
                    )
                except Exception:
                    pass

    def start_session(
        self,
        name: str = "",
        mode: str = "PAPER",
        allocated_capital: float = 300.0,
        order_size_shares: float = 20.0,
        notes: str = "",
    ) -> dict:
        """Starts a new isolated trading session with user-specified capital and mode."""
        is_paper = (mode.upper() == "PAPER")
        self.config.dry_run = is_paper
        self.config.order_size_shares = float(order_size_shares)
        self.paper_engine.order_size_shares = float(order_size_shares)

        if self.db:
            sess = self.db.create_session(
                name=name,
                mode=mode,
                allocated_capital=allocated_capital,
                order_size_shares=order_size_shares,
                notes=notes,
            )
            self.current_session_id = sess["session_id"]
        else:
            self.current_session_id = f"SESS-{int(time.time())}"
            sess = {
                "session_id": self.current_session_id,
                "name": name or "Local Session",
                "mode": mode,
                "allocated_capital": allocated_capital,
                "order_size_shares": order_size_shares,
                "status": "ACTIVE",
            }

        # Initialize inventory state fresh for this session
        self.inventory.reset_for_session(self.current_session_id, allocated_capital)
        self.is_trading_active = True
        logger.info(f"🚀 Trading Session '{self.current_session_id}' STARTED. Mode: {mode.upper()}, Capital: ${allocated_capital:.2f}")
        return sess

    def pause_trading(self) -> Optional[dict]:
        """Pauses active quoting and fills."""
        self.is_trading_active = False
        self.paper_engine.update_quotes(0.0, 0.0, allow_up=False, allow_down=False)
        sess = None
        if self.db and self.current_session_id != "STANDBY":
            sess = self.db.pause_session(self.current_session_id)
        logger.info(f"⏸ Trading Session PAUSED.")
        return sess

    def resume_trading(self) -> Optional[dict]:
        """Resumes quoting and fills for active session."""
        self.is_trading_active = True
        sess = None
        if self.db and self.current_session_id != "STANDBY":
            sess = self.db.resume_session(self.current_session_id)
        logger.info(f"▶ Trading Session RESUMED.")
        return sess

    def stop_session(self) -> Optional[dict]:
        """Stops and archives the current session."""
        self.is_trading_active = False
        self.paper_engine.update_quotes(0.0, 0.0, allow_up=False, allow_down=False)
        sess = None
        if self.db and self.current_session_id != "STANDBY":
            sess = self.db.stop_session(self.current_session_id)
        self.current_session_id = "STANDBY"
        logger.info(f"⏹ Trading Session STOPPED and archived.")
        return sess

    async def _on_binance_tick(self, feed: BinanceFeed):
        """Triggered whenever Binance sub-50ms spot tick arrives."""
        velocity = feed.get_velocity()
        pct_return = feed.get_percent_return()

        # Calculate Bayesian fair implied probability
        up_mid = (self.polymarket_feed.up_best_bid + self.polymarket_feed.up_best_ask) / 2.0
        self.fair_prob = self.fair_value_model.calculate_fair_probabilities(
            spot_velocity=velocity,
            spot_percent_return=pct_return,
            polymarket_up_mid=up_mid,
        )

        # Check for fills on tick arrivals if trading is active
        if self.is_trading_active and not self.inventory.is_stop_loss_triggered:
            if self.config.dry_run:
                filled = self.paper_engine.check_fills(
                    up_market_ask=self.polymarket_feed.up_best_ask,
                    up_last_trade=self.polymarket_feed.up_last_trade,
                    down_market_ask=self.polymarket_feed.down_best_ask,
                    down_last_trade=self.polymarket_feed.down_last_trade,
                    up_best_bid=self.polymarket_feed.up_best_bid,
                    down_best_bid=self.polymarket_feed.down_best_bid,
                    feed=self.polymarket_feed,
                )
                if filled:
                    for fill in filled:
                        self.last_fill_event = f"{fill['side']} @ ${fill['price']:.2f} ({fill['shares']:.1f} shs)"
                        inv_summary = self.inventory.get_summary()
                        self.recorder.log_trade(fill, inv_summary)

        await self._evaluate_and_quote()

    async def _on_polymarket_book(self, feed: PolymarketFeed):
        """Triggered whenever Polymarket order book updates."""
        # 1. Check for fills in paper engine ONLY if trading is actively enabled
        if self.is_trading_active and not self.inventory.is_stop_loss_triggered:
            if self.config.dry_run:
                filled = self.paper_engine.check_fills(
                    up_market_ask=feed.up_best_ask,
                    up_last_trade=feed.up_last_trade,
                    down_market_ask=feed.down_best_ask,
                    down_last_trade=feed.down_last_trade,
                    up_best_bid=feed.up_best_bid,
                    down_best_bid=feed.down_best_bid,
                    feed=feed,
                )

                if filled:
                    for fill in filled:
                        self.last_fill_event = f"{fill['side']} @ ${fill['price']:.2f} ({fill['shares']:.1f} shs)"
                        inv_summary = self.inventory.get_summary()
                        self.recorder.log_trade(fill, inv_summary)

        # 2. Recalculate optimal quotes
        await self._evaluate_and_quote()

    def _on_contract_rollover(self, old_slug: str, old_title: str, new_slug: str, new_title: str, winning_side: Optional[str] = None):
        """
        Called automatically when rolling over from an expired/settled contract to a fresh round.
        Settles any residual inventory from the old contract, books the final round payout into realized PnL,
        and initializes active inventory counters to 0.0 for the fresh upcoming contract round.
        """
        logger.info(f"🔄 CONTRACT ROLLOVER: Settling '{old_title}' -> Starting '{new_title}'")
        
        # 1. Settle residual shares of the concluding contract and reset active inventory to 0.0
        if hasattr(self, "inventory") and self.inventory:
            self.inventory.settle_contract_round(winning_side=winning_side, market_title=old_title)
        
        # 2. Reset active paper engine in-flight orders
        if hasattr(self, "paper_engine") and self.paper_engine:
            self.paper_engine.active_order_up = None
            self.paper_engine.active_order_down = None

    async def _evaluate_and_quote(self):
        """Calculates optimal bids enforcing cost ceilings, inventory caps, and daily stop-loss."""
        stoikov_skew = self.inventory.get_stoikov_skew()
        imbalance = self.inventory.net_imbalance

        self.current_quotes = self.quoter.calculate_quotes(
            q_up=self.fair_prob.get("q_up", 0.50),
            q_down=self.fair_prob.get("q_down", 0.50),
            stoikov_skew=stoikov_skew,
            net_imbalance=imbalance,
            up_avg_cost=self.inventory.up.avg_cost,
            down_avg_cost=self.inventory.down.avg_cost,
            up_best_bid=self.polymarket_feed.up_best_bid,
            down_best_bid=self.polymarket_feed.down_best_bid,
        )

        # Check if trading is disabled (STANDBY / PAUSED) or Circuit Breaker triggered
        if not self.is_trading_active or self.inventory.is_stop_loss_triggered:
            if not self.config.dry_run:
                await self.live_engine.cancel_all_orders()
            else:
                self.paper_engine.update_quotes(0.0, 0.0, allow_up=False, allow_down=False, feed=self.polymarket_feed)
            return

        allow_up = self.current_quotes["allow_quote_up"]
        allow_down = self.current_quotes["allow_quote_down"]

        # Expiry Safeguard for 5M/15M binary contracts:
        # In final 35s before bar settlement, freeze initiating fresh unhedged risk,
        # only quote to close/merge existing complete-set inventory.
        if self.polymarket_feed.market_end_time:
            secs_left = self.polymarket_feed.market_end_time - time.time()
            if secs_left < 35:
                if self.inventory.up.shares > self.inventory.down.shares:
                    allow_up = False
                elif self.inventory.down.shares > self.inventory.up.shares:
                    allow_down = False
                elif self.inventory.net_imbalance == 0:
                    allow_up = False
                    allow_down = False

        # Route orders to active engine (Live CLOB or Paper Simulator)
        if not self.config.dry_run:
            await self.live_engine.sync_orders(
                quote_up=self.current_quotes["quote_up"],
                quote_down=self.current_quotes["quote_down"],
                allow_up=allow_up,
                allow_down=allow_down,
            )
        else:
            self.paper_engine.update_quotes(
                quote_up=self.current_quotes["quote_up"],
                quote_down=self.current_quotes["quote_down"],
                allow_up=allow_up,
                allow_down=allow_down,
                feed=self.polymarket_feed,
            )

    def render_dashboard(self) -> Table:
        """Constructs a real-time rich dashboard table with risk monitoring."""
        table = Table(
            title="[bold green]Polymarket Quant Engine[/bold green] - [bold yellow]Binance Feed & Risk Protected[/bold yellow]",
            expand=True,
        )

        table.add_column("Category", style="cyan", justify="left")
        table.add_column("Metrics & Real-Time Values", style="white", justify="left")

        # 1. Binance Feed
        bn_p = self.binance_feed.current_price
        bn_v = self.binance_feed.get_velocity()
        bn_ret = self.binance_feed.get_percent_return() * 100.0
        table.add_row(
            "[bold yellow]Binance Direct Stream[/bold yellow]",
            f"Symbol: {self.config.binance_symbol} | Price: ${bn_p:,.2f} | Velocity: {bn_v:+.2f} $/s | 10s Ret: {bn_ret:+.3f}%",
        )

        # 2. Bayesian Model
        q_up = self.fair_prob.get("q_up", 0.50)
        q_down = self.fair_prob.get("q_down", 0.50)
        mom = self.fair_prob.get("momentum_score", 0.0)
        table.add_row(
            "[bold magenta]Bayesian Fair Value[/bold magenta]",
            f"Fair P(UP): {q_up:.3f} | Fair P(DOWN): {q_down:.3f} | Momentum Score: {mom:+.2f}",
        )

        # 3. Polymarket Book
        pm_title = self.polymarket_feed.market_title[:45]
        up_bid, up_ask = self.polymarket_feed.up_best_bid, self.polymarket_feed.up_best_ask
        dn_bid, dn_ask = self.polymarket_feed.down_best_bid, self.polymarket_feed.down_best_ask
        table.add_row(
            "[bold blue]Polymarket CLOB[/bold blue]",
            f"Market: '{pm_title}'\nUP Book: Bid ${up_bid:.2f} / Ask ${up_ask:.2f} | DOWN Book: Bid ${dn_bid:.2f} / Ask ${dn_ask:.2f}",
        )

        # 4. Quoting Engine & Complete-Set Target
        q_up_val = self.current_quotes.get("quote_up", 0.0)
        q_dn_val = self.current_quotes.get("quote_down", 0.0)
        p_cost = self.current_quotes.get("projected_cost", 0.0)
        p_edge = self.current_quotes.get("projected_edge", 0.0) * 100.0
        table.add_row(
            "[bold cyan]Optimal Quoting[/bold cyan]",
            f"Limit Bid UP: ${q_up_val:.2f} | Limit Bid DOWN: ${q_dn_val:.2f}\n"
            f"Complete Set Cost: ${p_cost:.3f} (Max: ${self.config.max_combined_cost:.3f}) | Edge: {p_edge:.2f}%",
        )

        # 5. Inventory, PnL & Risk Circuit Breakers
        inv = self.inventory.get_summary()
        status_text = "[bold red]STOP-LOSS TRIGGERED[/bold red]" if inv["is_stop_loss_triggered"] else "[bold green]ACTIVE - PROTECTED[/bold green]"
        table.add_row(
            "[bold green]Inventory & Risk Status[/bold green]",
            f"Holdings: {inv['up_shares']} UP | {inv['down_shares']} DOWN | Imbalance: {inv['net_imbalance']:+0.1f} (Cap: ±{self.config.max_inventory_imbalance})\n"
            f"Complete Sets Merged: [bold green]{inv['complete_sets_merged']}[/bold green] | "
            f"Realized Arb PnL: [bold green]+${inv['realized_arb_pnl']:.2f}[/bold green] | Status: {status_text}",
        )

        return table

    async def start(self):
        """Starts all async tasks and renders the live dashboard."""
        self._running = True
        logger.info("Starting Polymarket Quant Engine with Binance Feed...")

        # 0. Initialize Polymarket Manager & Live Engine
        await self.poly_manager.initialize()
        await self.live_engine.initialize()

        # 1. Start Web Dashboard if enabled
        if self.config.enable_dashboard:
            self.dashboard = DashboardServer(
                engine=self,
                host=self.config.dashboard_host,
                port=self.config.dashboard_port,
                auth_token=self.config.dashboard_auth_token,
            )
            asyncio.create_task(self.dashboard.start())

        # 2. Start Feeds
        feed_tasks = [
            asyncio.create_task(self.binance_feed.start()),
            asyncio.create_task(self.polymarket_feed.start()),
        ]

        try:
            if HAVE_RICH and console:
                with Live(self.render_dashboard(), refresh_per_second=2, console=console) as live:
                    while self._running:
                        live.update(self.render_dashboard())
                        await asyncio.sleep(0.5)
            else:
                print("Starting Polymarket Quant Engine (Console Mode)...")
                while self._running:
                    inv = self.inventory.get_summary()
                    bn_p = self.binance_feed.current_price
                    bn_v = self.binance_feed.get_velocity()
                    q_up_val = self.current_quotes.get("quote_up", 0.0)
                    q_dn_val = self.current_quotes.get("quote_down", 0.0)
                    p_cost = self.current_quotes.get("projected_cost", 0.0)
                    print(
                        f"[TICK] Binance: ${bn_p:,.2f} ({bn_v:+.2f}$/s) | "
                        f"Quotes: UP=${q_up_val:.2f} DN=${q_dn_val:.2f} (Sum=${p_cost:.3f}) | "
                        f"Merged Sets: {inv['complete_sets_merged']} | PnL: +${inv['realized_arb_pnl']:.2f}"
                    )
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            for t in feed_tasks:
                t.cancel()
            await self.live_engine.cancel_all_orders()
            await self.binance_feed.stop()
            await self.polymarket_feed.stop()
            logger.info("Engine stopped safely.")


def run():
    config = BotConfig()
    engine = PolymarketQuantEngine(config)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_sigint():
        print("\nShutting down bot safely...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_sigint)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(engine.start())
    except KeyboardInterrupt:
        print("\nShutdown complete.")


if __name__ == "__main__":
    run()
