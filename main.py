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
from execution.paper_engine import PaperTradingEngine
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
            on_book_update_callback=self._on_polymarket_book,
        )

        # Strategy State
        self.current_quotes: dict = {}
        self.fair_prob: dict = {"q_up": 0.50, "q_down": 0.50, "momentum_score": 0.0}
        self.last_fill_event: str = "None"
        self._running: bool = False

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

        await self._evaluate_and_quote()

    async def _on_polymarket_book(self, feed: PolymarketFeed):
        """Triggered whenever Polymarket order book updates."""
        # 1. Check for fills in paper engine
        filled = self.paper_engine.check_fills(
            up_market_ask=feed.up_best_ask,
            up_last_trade=feed.up_last_trade,
            down_market_ask=feed.down_best_ask,
            down_last_trade=feed.down_last_trade,
        )

        if filled:
            for fill in filled:
                self.last_fill_event = f"{fill['side']} @ ${fill['price']:.2f} ({fill['shares']:.0f} shs)"
                inv_summary = self.inventory.get_summary()
                self.recorder.log_trade(fill, inv_summary)

        # 2. Recalculate optimal quotes
        await self._evaluate_and_quote()

    async def _evaluate_and_quote(self):
        """Calculates optimal bids enforcing cost ceilings, inventory caps, and daily stop-loss."""
        # Circuit Breaker: Daily Stop-Loss Check
        if self.inventory.is_stop_loss_triggered:
            # Emergency Stop: Cancel all active limit orders
            self.paper_engine.update_quotes(0.0, 0.0, allow_up=False, allow_down=False)
            return

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

        # Update paper trading virtual orders
        self.paper_engine.update_quotes(
            quote_up=self.current_quotes["quote_up"],
            quote_down=self.current_quotes["quote_down"],
            allow_up=self.current_quotes["allow_quote_up"],
            allow_down=self.current_quotes["allow_quote_down"],
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
