"""
Configuration parameters for the Polymarket Quant Engine with Binance Price Feed
and Mandatory Risk Rules for $300 Capital Protection.
"""
from dataclasses import dataclass, field
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class BotConfig:
    # Execution Mode: Set to True for zero-risk paper trading / simulation
    dry_run: bool = True

    # 1. Primary Reference Asset Feed (Binance Direct High-Speed WebSocket)
    binance_symbol: str = "BTCUSDT"  # Binance spot pair (e.g. BTCUSDT, ETHUSDT, SOLUSDT)

    # 2. Polymarket Market Target (15-Minute Crypto Up/Down Contracts)
    target_market_slug: str = "btc-15m-up-down"
    token_id_up: str = ""
    token_id_down: str = ""
    auto_discover_active_market: bool = True  # Automatically discover active 15m/5m BTC market

    # 3. Quantitative Strategy Parameters
    target_edge_per_share: float = 0.040  # Target 4.0 cents edge per complete set (4.0% - 5.0%)
    max_combined_cost: float = 0.960       # Hard ceiling: Quote_UP + Quote_DOWN <= 0.960 (Never buy > 96c)
    min_bid_price: float = 0.05           # Lowest acceptable bid price ($0.05)
    max_bid_price: float = 0.95           # Highest acceptable bid price ($0.95)

    # 4. Mandatory Risk Rules & Sizing (Strict $300 Capital Safeguards)
    order_size_shares: float = 25.0        # Rule 1: Max ~$11.25 per individual order (25 shares)
    max_inventory_imbalance: float = 100.0 # Rule 2: Strict Inventory Cap (Max 100 unhedged shares / ~$46 max exposure)
    daily_stop_loss_usd: float = 30.0      # Rule 3: Daily Stop-Loss ($30 max daily loss / 10% of $300 capital)
    inventory_risk_aversion: float = 0.003 # Avellaneda-Stoikov inventory penalty factor (gamma)

    # Momentum & Bayesian Model Parameters
    velocity_lookback_seconds: int = 10   # Window to measure spot price velocity (dPrice/dt)
    momentum_sensitivity: float = 2.5     # Scaling factor for Bayesian probability shift from spot velocity

    # Wallet / API Settings for Live Execution (When dry_run is False)
    private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    polygon_rpc_url: str = field(default_factory=lambda: os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"))
    api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_PASSPHRASE", ""))

    # Data Logging
    log_trades_to_csv: bool = True
    trade_log_file: str = "trades_simulated.csv"
