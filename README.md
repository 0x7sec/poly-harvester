# Polkadot-Frog Style Polymarket Quant Bot 🐸

A modular, high-performance quantitative trading engine designed for Polymarket's short-term BTC/ETH Up/Down binary markets. It implements **Two-Sided Intramarket Volatility Harvesting**, **Gnosis CTF Complete-Set Accumulation**, **Avellaneda-Stoikov Inventory Skewing**, and **KuCoin sub-second spot velocity tracking**.

---

## Key Features

1. **Gnosis CTF Complete-Set Merging:**
   * Automatically pairs UP shares ($P_{\text{UP}}$) and DOWN shares ($P_{\text{DOWN}}$) accumulated across volatility swings.
   * Merges them into Complete Sets for guaranteed $\$1.00\text{ USDC}$ redemptions whenever $P_{\text{UP}} + P_{\text{DOWN}} \le \$0.985$.
2. **Sub-Second KuCoin WebSocket Feed:**
   * Streams live spot ticks for `BTC-USDT` to calculate real-time price velocity ($\Delta S / \Delta t$) and shift the Bayesian fair probability before Polymarket order books adjust.
3. **Avellaneda-Stoikov Inventory Rebalancing:**
   * Dynamically skews quote prices based on inventory imbalance ($\text{Shares}_{\text{UP}} - \text{Shares}_{\text{DOWN}}$) to prevent holding toxic inventory into expiry.
4. **Zero-Risk Live Shadow / Paper Trading Mode:**
   * Connects to live feeds and simulates realistic limit order fills, complete set merges, fee deductions, and net PnL in real time without risking capital.
5. **Interactive Terminal Dashboard:**
   * Built with `rich` to display live KuCoin ticks, Bayesian probability, Polymarket order books, active quotes, and accumulated arbitrage PnL.

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Automated Verification Tests
```bash
python test_engine.py
```

### 3. Run the Bot in Paper Trading Mode (Default: Zero Risk)
```bash
python main.py
```

---

## Configuration (`config.py`)

| Parameter | Default | Description |
| :--- | :---: | :--- |
| `dry_run` | `True` | Set to `True` for paper trading, `False` for live execution. |
| `kucoin_symbol` | `"BTC-USDT"` | Reference spot pair on KuCoin (`BTC-USDT`, `ETH-USDT`). |
| `target_edge_per_share` | `0.015` | Minimum target profit per complete set (1.5 cents = 1.5%). |
| `max_combined_cost` | `0.985` | Hard limit for `Quote_UP + Quote_DOWN <= 0.985`. |
| `order_size_shares` | `50.0` | Size in shares per limit order (approx. $25–$50). |
| `max_inventory_imbalance` | `150.0` | Max net share difference before pausing new bids on the heavy side. |
| `inventory_risk_aversion` | `0.002` | Stoikov $\gamma$ factor for price skewing. |

---

## Architecture

```
polymarket-quant/
├── config.py              # Configuration & strategy parameters
├── feeds/
│   ├── kucoin_feed.py     # KuCoin public WebSocket client (spot & velocity)
│   └── polymarket_feed.py # Polymarket CLOB WebSocket & market auto-discovery
├── models/
│   ├── fair_value.py      # Bayesian fair probability calculator
│   ├── inventory.py       # Complete-set tracking & CTF merging
│   └── quoter.py          # Optimal limit order pricing & cost constraints
├── execution/
│   ├── paper_engine.py    # Live matching simulator (Zero-Risk)
│   └── live_engine.py     # Live execution router (py-sdk)
├── backtest/
│   └── recorder.py        # CSV trade and performance logger
├── test_engine.py         # Unit & integration test suite
├── main.py                # Main orchestrator & real-time dashboard
└── requirements.txt       # Dependencies
```
