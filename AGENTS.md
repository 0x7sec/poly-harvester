# Poly-Harvester: Autonomous Polymarket Quantitative Arbitrage & Market Making Engine

Welcome to the **Poly-Harvester** codebase. This document serves as the complete technical manual, architecture reference, and operations guide for AI agents and human developers interacting with, maintaining, or extending this project.

---

## 1. System Overview & Core Philosophy

**Poly-Harvester** is an institutional-grade, high-frequency algorithmic market-making and arbitrage engine designed for Polymarket's fast-settling binary prediction markets (such as Bitcoin/Ethereum 5-minute, 15-minute, and hourly contracts).

### The Core Alpha Mechanism: Complete-Set Arbitrage
On Polymarket, binary prediction markets resolve to binary outcomes: $\mathbf{1\text{ UP Token} + 1\text{ DOWN Token} \equiv \$1.000\text{ USDC Cash}}$.
Rather than taking directional gambling bets, `poly-harvester` operates as a **delta-neutral market maker**:
1. **Sub-50ms Spot Velocity Lead**: Uses a direct WebSocket tick stream from Binance to compute Bayesian implied fair probabilities milliseconds ahead of Polymarket CLOB adjustments.
2. **Dual-Sided Maker Quoting**: Posts post-only limit bids inside the spread such that $\text{Cost}_{\text{UP}} + \text{Cost}_{\text{DOWN}} \le \$0.960$.
3. **Avellaneda-Stoikov Inventory Skewing**: Dynamically adjusts quote spreads based on held inventory ($\Delta = \text{UP} - \text{DOWN}$) to continuously return net inventory to zero.
4. **Automated On-Chain CTF Merging**: Whenever $\ge 10$ pairs of UP and DOWN tokens are accumulated, the engine executes smart contract position redemptions (`merge_positions`) on Polygon, returning capital to liquid cash and locking in guaranteed arbitrage profit.

---

## 2. Codebase Architecture & File Structure

```
poly-harvester/
├── main.py                     # Primary engine loop, lifecycle controller, and CLI dashboard
├── config.py                   # Central BotConfig dataclass with environment overrides
├── mcp_server.py               # Standalone Model Context Protocol (MCP) server & tool registry
├── requirements.txt            # Python dependencies
├── test_engine.py              # Quantitative & execution unit test suite
├── test_storage.py             # SQLite WAL database & session manager test suite
│
├── models/                     # Quantitative & API Models
│   ├── fair_value.py           # Bayesian spot momentum logistic probability estimator
│   ├── quoter.py               # Optimal quoting engine with Stoikov skew & cost constraints
│   ├── inventory.py            # Position manager, complete-set merger, & daily stop-loss
│   └── polymarket_client.py    # Official SDK client wrapper, token-bucket limiter, & geoblock checker
│
├── execution/                  # Execution & Microstructure Engines
│   ├── live_engine.py          # EIP-712 live order router, post-only maker, & CLOB fill reconciler
│   └── paper_engine.py         # L2 order book depth simulator with queue priority & CTF fee tracking
│
├── feeds/                      # Real-Time Data Ingestion
│   ├── binance_feed.py         # Sub-50ms Binance spot tick WebSocket & velocity Z-score
│   └── polymarket_feed.py      # Polymarket CLOB book stream, market discovery, & rollover handler
│
├── storage/                    # Persistence Layer
│   └── database.py             # SQLite WAL database manager for sessions, trades, & MCP logs
│
└── dashboard/                  # Cyber-Quant Web Interface & REST/WS API
    ├── server.py               # Async aiohttp server, WebSocket streaming, & auth controller
    └── static/                 # Single-page web client (HTML5, Vanilla CSS, JS)
        ├── index.html          # Cyber-quant UI layout & modal templates
        ├── style.css           # Glassmorphic dark-mode cyber theme
        └── app.js              # Real-time WebSocket streaming, Canvas sparkline, & session manager
```

---

## 3. Detailed Component Breakdown

### 3.1 Quantitative Models (`models/`)
* **`fair_value.py` (`FairValueModel`)**:
  Maps Binance 10-second spot price return into a logit-space Bayesian probability update:
  $$z = \ln\left(\frac{q_{\text{mid}}}{1 - q_{\text{mid}}}\right) + \lambda \cdot (\Delta_{\text{spot}} \cdot 1000), \quad q_{\text{UP}} = \frac{1}{1 + e^{-z}}$$
* **`quoter.py` (`QuotingEngine`)**:
  Calculates maker limit bids strictly enforcing the hard cost ceiling ($\le \$0.960$). Adjusts bids using the Stoikov inventory penalty ($\text{Skew} = \gamma \cdot \Delta$). Freezes quoting on dead legs when probability reaches extreme levels ($\ge 88\%$).
* **`inventory.py` (`InventoryManager`)**:
  Maintains exact position balances, tracks average costs, calculates realized PnL, executes contract rollover settlements upon round resolution, and triggers emergency shutdown if daily drawdown hits $-\$25.00$.
* **`polymarket_client.py` (`PolymarketManager`)**:
  Integrates the official `polymarket` SDK (`AsyncPublicClient` / `AsyncSecureClient`). Implements token-bucket rate limiting (40 req/s orders, 80 req/s cancels), geographic compliance auditing (`https://polymarket.com/api/geoblock`), and automatic trading approvals on Polygon.

### 3.2 Execution Engines (`execution/`)
* **`live_engine.py` (`LiveTradingEngine`)**:
  Constructs and transmits EIP-712 cryptographically signed orders with `post_only = True`. Reconciles live order fills directly against the Polymarket CLOB (`get_order`), eliminating phantom fills. Automatically invokes `merge_positions` on-chain when pairs are accumulated.
* **`paper_engine.py` (`PaperTradingEngine`)**:
  Provides an institutional-grade paper simulator. Walks real Polymarket L2 order books, tracks queue depth ahead, injects simulated in-flight wire latency (50ms), and deducts maker/taker CTF exchange fees.

### 3.3 Feeds (`feeds/`)
* **`binance_feed.py` (`BinanceFeed`)**:
  Direct raw WebSocket connection to `wss://stream.binance.com:9443/ws/btcusdt@trade` with sub-50ms tick latency.
* **`polymarket_feed.py` (`PolymarketFeed`)**:
  Connects to Polymarket CLOB book streams, auto-discovers active 5M/15M markets via Gamma API, and handles seamless rollover to fresh contracts every round.

### 3.4 Storage & Database (`storage/`)
* **`database.py` (`DatabaseManager`)**:
  Thread-safe SQLite database running in WAL mode (`data/poly_harvester.db`). Manages:
  - Isolated trading sessions (`sessions` table)
  - Trade execution ledgers (`trades` table)
  - Complete-set merge events (`complete_sets` table)
  - MCP API keys and tool invocation audit logs (`mcp_api_keys`, `mcp_call_logs`)

### 3.5 Web Dashboard & API (`dashboard/`)
* Modern dark-mode cyber-quant interface accessible at `http://localhost:8443`.
* Real-time WebSocket streaming at 20Hz with Canvas sparklines, latency meters, dynamic session controls (Start / Pause / Stop), and dropdown persistence in browser `localStorage`.

---

## 4. MCP Agent Manager & Remote Tool Calling

`poly-harvester` provides a first-class Model Context Protocol (MCP) server enabling autonomous AI agents to monitor, audit, and control trading activity remotely.

### Available MCP Tools:
| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| `poly_get_status` | None | Returns complete live telemetry (Binance price, Bayesian probability, quotes, inventory, active session). |
| `poly_list_positions` | None | Lists held UP and DOWN share balances, average entry costs, and total capital deployed. |
| `poly_list_trades` | `limit: int`, `session_id: str` | Queries historical executed order fills from the SQLite audit database. |
| `poly_list_complete_sets` | `limit: int`, `session_id: str` | Queries on-chain complete-set redemption events and locked arbitrage profit. |
| `poly_update_risk_limits` | `order_size_shares`, `max_imbalance`, `max_combined_cost`, `daily_stop_loss_usd` | Dynamically updates runtime risk constraints without restarting the engine. |
| `poly_pause_trading` | None | Immediately pauses active quoting and cancels resting limit orders. |
| `poly_resume_trading` | None | Resumes active quoting for the current session. |
| `poly_emergency_stop` | None | Triggers the emergency circuit breaker, canceling all open orders and halting the engine. |

Agents can connect via:
* **HTTP JSON-RPC 2.0**: `POST /api/mcp/rpc` (Headers: `X-MCP-API-Key: <key>`)
* **SSE Stream**: `GET /mcp/sse?api_key=<key>`
* **REST Endpoints**: `POST /api/mcp/execute/<tool_name>`

---

## 5. Development & Operations Guide

### Starting the Engine
```bash
python main.py
```
* On boot, the engine initializes feeds in **STANDBY mode** (zero financial risk).
* Open the Web Dashboard at [http://localhost:8443](http://localhost:8443).
* Click **`Start Trading`** to launch a session in **`PAPER`** or **`LIVE`** mode.

### Running Test Suites
```bash
# Run all unit and integration tests
python -m unittest test_engine.py test_storage.py

# Validate frontend JavaScript syntax
node -c dashboard/static/app.js
```

### Risk Boundaries & Invariants (DO NOT MODIFY WITHOUT AUDIT)
1. **Cost Ceiling**: $\text{Bid}_{\text{UP}} + \text{Bid}_{\text{DOWN}} \le \$0.960$. Never allow combined bids to exceed this threshold.
2. **Post-Only Maker Mandate**: All live orders must carry `post_only = True`.
3. **Daily Stop-Loss**: If net PnL reaches $-\$25.00$, the circuit breaker must halt all trading activity immediately.
4. **Pre-Expiry Freeze**: In the final 35 seconds before market bar settlement, do not initiate fresh unhedged risk; only quote to complete existing sets.
