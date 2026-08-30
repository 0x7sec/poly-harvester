"""
Configuration parameters for the Poly-Harvester Quant Engine with Binance Price Feed,
Mandatory Risk Safeguards, and Secure Remote Dashboard / MCP Server.
"""
from dataclasses import dataclass, field
from typing import Optional
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
    binance_symbol: str = "BTCUSDT"  # Binance spot pair (e.g. BTCUSDT, ETHUSDT)

    # 2. Polymarket Market Target (15-Minute Crypto Up/Down Contracts)
    target_market_slug: str = "btc-15m-up-down"
    token_id_up: str = ""
    token_id_down: str = ""
    auto_discover_active_market: bool = True  # Automatically discover active 15m/5m BTC market

    # 3. Quantitative Strategy Parameters (Polkadot-Frog HFT Profile)
    target_edge_per_share: float = 0.040  # Target 4.0 cents edge per complete set (4.0% - 5.0% gross margin)
    max_combined_cost: float = 0.960       # Hard ceiling: Quote_UP + Quote_DOWN <= 0.960 (Target 0.950 - 0.965)
    min_bid_price: float = 0.05           # Lowest acceptable bid price ($0.05)
    max_bid_price: float = 0.95           # Highest acceptable bid price ($0.95)

    # 4. Mandatory Risk Rules & Sizing (Polkadot-Frog Style Scaled Clips)
    order_size_shares: float = 25.0        # Order size: 25.0 shares (~$12.00 - $25.00 per individual clip)
    max_inventory_imbalance: float = 75.0  # Directional Residual Cap: Max 75.0 unhedged shares (~$35.00 exposure)
    daily_stop_loss_usd: float = 25.0      # Daily Stop-Loss ($25.00 max daily drawdown circuit breaker)
    inventory_risk_aversion: float = 0.003 # Avellaneda-Stoikov inventory penalty factor (gamma)

    # Momentum & Bayesian Model Parameters
    velocity_lookback_seconds: int = 10   # Window to measure spot price velocity (dPrice/dt)
    momentum_sensitivity: float = 2.5     # Scaling factor for Bayesian probability shift from spot velocity

    # 5. Secure Dashboard & MCP Server Configuration
    enable_dashboard: bool = True
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8443
    dashboard_auth_token: Optional[str] = field(
        default_factory=lambda: os.getenv("DASHBOARD_AUTH_TOKEN") or None
    )

    # Wallet / API Settings for Live Execution (When dry_run is False)
    private_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_PRIVATE_KEY", ""))
    wallet_address: str = field(default_factory=lambda: os.getenv("POLYMARKET_WALLET_ADDRESS", ""))
    proxy_url: str = field(default_factory=lambda: os.getenv("POLYMARKET_PROXY_URL", ""))
    polygon_rpc_url: str = field(default_factory=lambda: os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"))
    api_key: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLYMARKET_API_PASSPHRASE", ""))

    # Data Logging
    log_trades_to_csv: bool = True
    trade_log_file: str = "trades_simulated.csv"
