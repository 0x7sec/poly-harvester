"""
Model Context Protocol (MCP) Server for Poly-Harvester.
Exposes real-time quant telemetry, SQLite trade history, complete-set ledger,
performance analytics, risk control, and emergency circuit breaker tools over standard JSON-RPC 2.0 stdio.
"""
import asyncio
import json
import logging
import math
import sys
from typing import Any, Dict, List, Optional

# Ensure UTF-8 on Windows
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

logger = logging.getLogger("PolyHarvesterMCP")


class PolyHarvesterMCPServer:
    """
    Standard MCP (Model Context Protocol) Server for Poly-Harvester.
    Enables AI agents and remote clients to inspect live telemetry, query SQLite historical records,
    and execute control overrides with persistent Diskcache backup.
    """

    def __init__(self, engine=None):
        self.engine = engine

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "poly_get_status",
                "description": "Returns real-time engine status: Binance spot price, velocity, active quotes, complete sets merged, and net realized PnL.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "poly_get_inventory",
                "description": "Returns current position breakdown: UP shares, DOWN shares, average cost, net imbalance, and Stoikov inventory skew.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "poly_get_trades_history",
                "description": "Returns historical executed trades from SQLite database with pagination.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of trades to return (default: 25)."},
                        "offset": {"type": "integer", "description": "Offset index for pagination (default: 0)."}
                    },
                },
            },
            {
                "name": "poly_get_complete_sets",
                "description": "Returns the complete-set merge ledger from SQLite, showing $1.00 redemptions, costs, and locked profits.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Number of records to return (default: 25)."}
                    },
                },
            },
            {
                "name": "poly_get_analytics",
                "description": "Returns cumulative quantitative performance analytics (total volume, sets merged, net PnL, average pair cost) from SQLite.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "poly_emergency_stop",
                "description": "Emergency circuit breaker: immediately cancels all active limit quotes, halts quoting, and freezes trading.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Reason for triggering emergency stop."}
                    },
                },
            },
            {
                "name": "poly_resume_trading",
                "description": "Resets stop-loss / pause status and resumes active market making and complete-set accumulation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "poly_update_risk_limits",
                "description": "Dynamically updates risk thresholds (order size, inventory cap, daily stop-loss, max complete-set cost) and persists to SQLite & Diskcache.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "order_size_shares": {"type": "number", "description": "Size in shares per limit order."},
                        "max_inventory_imbalance": {"type": "number", "description": "Max unhedged shares allowed on one side."},
                        "max_combined_cost": {"type": "number", "description": "Hard ceiling for Quote_UP + Quote_DOWN (e.g. 0.960)."},
                        "daily_stop_loss_usd": {"type": "number", "description": "Max allowable daily drawdown in USD."},
                    },
                },
            },
            {
                "name": "poly_run_backtest",
                "description": (
                    "Runs a historical backtest of the complete-set arbitrage + directional-residual "
                    "strategy against BTC price ticks. Measures whether the edge is real: complete-set "
                    "arb profit, directional residual P&L/win-rate, and model calibration (Brier score, "
                    "accuracy). Provide a CSV path (timestamp,price) or use a synthetic random walk."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "data_path": {"type": "string", "description": "Path to a CSV with timestamp,price columns. Omit for synthetic data."},
                        "contract_window": {"type": "integer", "description": "Contract window in seconds (default 300 = 5m)."},
                        "order_size_shares": {"type": "number", "description": "Shares per clip (default 25)."},
                        "max_combined_cost": {"type": "number", "description": "Cost ceiling for arb (default 0.96)."},
                        "min_edge": {"type": "number", "description": "Min edge per set to take arb (default 0.01)."},
                        "fee_bps": {"type": "number", "description": "Trading fee in basis points (default 0)."},
                        "synthetic_duration": {"type": "integer", "description": "Seconds of synthetic data if no CSV (default 3600)."},
                        "seed": {"type": "integer", "description": "RNG seed for synthetic data (default 42)."},
                    },
                },
            },
            {
                "name": "poly_get_leg_risk",
                "description": (
                    "Returns the current directional-residual risk decision: which leg is exposed, "
                    "the exposure size, and whether the engine should HOLD the residual or HEDGE it "
                    "back into a complete set, based on the fair-value model."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def handle_tool_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self.engine:
            return {"error": "Engine is not currently running or attached."}

        if name == "poly_get_status":
            inv = self.engine.inventory.get_summary()
            quotes = self.engine.current_quotes
            return {
                "binance_price": self.engine.binance_feed.current_price,
                "spot_velocity_usd_per_sec": self.engine.binance_feed.get_velocity(),
                "fair_prob_up": self.engine.fair_prob.get("q_up", 0.50),
                "fair_prob_down": self.engine.fair_prob.get("q_down", 0.50),
                "active_quotes": {
                    "quote_up": quotes.get("quote_up", 0.0),
                    "quote_down": quotes.get("quote_down", 0.0),
                    "projected_cost": quotes.get("projected_cost", 0.0),
                    "projected_edge_pct": round(quotes.get("projected_edge", 0.0) * 100.0, 2),
                },
                "complete_sets_merged": inv["complete_sets_merged"],
                "realized_arb_pnl": inv["realized_arb_pnl"],
                "net_pnl": inv["net_pnl"],
                "status": "STOPPED" if inv["is_stop_loss_triggered"] else "ACTIVE",
            }

        elif name == "poly_get_inventory":
            return self.engine.inventory.get_summary()

        elif name == "poly_get_trades_history":
            limit = int(arguments.get("limit", 25))
            offset = int(arguments.get("offset", 0))
            if hasattr(self.engine, "db") and self.engine.db:
                trades = self.engine.db.get_recent_trades(limit=limit, offset=offset)
            else:
                trades = self.engine.paper_engine.fill_history[-limit:]
            return {"total_returned": len(trades), "trades": trades}

        elif name == "poly_get_complete_sets":
            limit = int(arguments.get("limit", 25))
            if hasattr(self.engine, "db") and self.engine.db:
                sets = self.engine.db.get_complete_sets(limit=limit)
            else:
                sets = []
            return {"total_returned": len(sets), "complete_sets": sets}

        elif name == "poly_get_analytics":
            if hasattr(self.engine, "db") and self.engine.db:
                return self.engine.db.get_analytics()
            return self.engine.inventory.get_summary()

        elif name == "poly_emergency_stop":
            self.engine.inventory.is_stop_loss_triggered = True
            self.engine.paper_engine.update_quotes(0.0, 0.0, allow_up=False, allow_down=False)
            reason = arguments.get("reason", "Manual emergency stop via MCP")
            if hasattr(self.engine, "cache") and self.engine.cache:
                self.engine.cache.set_bot_status(is_paused=True, is_stop_loss=True, reason=reason)
            return {"success": True, "message": f"Trading halted and all orders canceled. Reason: {reason}"}

        elif name == "poly_resume_trading":
            self.engine.inventory.is_stop_loss_triggered = False
            if hasattr(self.engine, "cache") and self.engine.cache:
                self.engine.cache.set_bot_status(is_paused=False, is_stop_loss=False, reason="Resumed via MCP")
            return {"success": True, "message": "Trading resumed. Market making engine active."}

        elif name == "poly_update_risk_limits":
            updated = []
            updated_dict = {}

            # Risk parameter bounds: field -> (min, max). Rejects non-numeric,
            # NaN/Inf, and out-of-range values so the cost ceiling / stop-loss
            # invariants can't be silently weakened via MCP.
            risk_bounds = {
                "order_size_shares": (0.0, 100000.0),
                "max_inventory_imbalance": (0.0, 100000.0),
                "max_combined_cost": (0.01, 0.99),
                "daily_stop_loss_usd": (1.0, 1000000.0),
            }

            def _validate(field_name: str):
                raw = arguments[field_name]
                try:
                    v = float(raw)
                except (TypeError, ValueError):
                    return None, f"Invalid numeric value for '{field_name}': {raw!r}"
                if not math.isfinite(v):
                    return None, f"'{field_name}' must be a finite number (got NaN/Inf)."
                lo, hi = risk_bounds[field_name]
                if not (lo <= v <= hi):
                    return None, f"'{field_name}' must be between {lo} and {hi} (got {v})."
                return v, None

            if "order_size_shares" in arguments:
                val, err = _validate("order_size_shares")
                if err:
                    return {"error": err}
                self.engine.config.order_size_shares = val
                self.engine.paper_engine.order_size_shares = val
                updated.append(f"order_size={val}")
                updated_dict["order_size_shares"] = val

            if "max_inventory_imbalance" in arguments:
                val, err = _validate("max_inventory_imbalance")
                if err:
                    return {"error": err}
                self.engine.config.max_inventory_imbalance = val
                self.engine.inventory.max_imbalance = val
                self.engine.quoter.max_imbalance = val
                updated.append(f"max_imbalance={val}")
                updated_dict["max_inventory_imbalance"] = val

            if "max_combined_cost" in arguments:
                val, err = _validate("max_combined_cost")
                if err:
                    return {"error": err}
                self.engine.config.max_combined_cost = val
                self.engine.quoter.max_combined_cost = val
                self.engine.inventory.max_combined_cost = val
                updated.append(f"max_cost={val}")
                updated_dict["max_combined_cost"] = val

            if "daily_stop_loss_usd" in arguments:
                val, err = _validate("daily_stop_loss_usd")
                if err:
                    return {"error": err}
                self.engine.config.daily_stop_loss_usd = val
                self.engine.inventory.daily_stop_loss = val
                updated.append(f"stop_loss=${val}")
                updated_dict["daily_stop_loss_usd"] = val

            # Persist to diskcache & SQLite
            if hasattr(self.engine, "cache") and self.engine.cache:
                current_cached = self.engine.cache.get_runtime_config() or {}
                current_cached.update(updated_dict)
                self.engine.cache.set_runtime_config(current_cached)

            if hasattr(self.engine, "db") and self.engine.db:
                self.engine.db.set_state("runtime_config", updated_dict)

            return {"success": True, "updated_parameters": updated}

        elif name == "poly_run_backtest":
            from strategy_backtest import run_backtest

            data_path = arguments.get("data_path")
            contract_window = int(arguments.get("contract_window", 300))
            order_size_shares = float(arguments.get("order_size_shares", 25.0))
            max_combined_cost = float(arguments.get("max_combined_cost", 0.96))
            min_edge = float(arguments.get("min_edge", 0.01))
            fee_bps = float(arguments.get("fee_bps", 0.0))
            synthetic_duration = int(arguments.get("synthetic_duration", 3600))
            seed = arguments.get("seed", 42)

            try:
                summary = run_backtest(
                    data_path=data_path,
                    contract_window=contract_window,
                    order_size_shares=order_size_shares,
                    max_combined_cost=max_combined_cost,
                    min_edge=min_edge,
                    fee_bps=fee_bps,
                    synthetic_duration=synthetic_duration,
                    seed=seed,
                )
                return {"status": "SUCCESS", "backtest": summary}
            except Exception as e:
                return {"status": "ERROR", "error": str(e)}

        elif name == "poly_get_leg_risk":
            if hasattr(self.engine, "inventory") and self.engine.inventory:
                fair_q_up = getattr(self.engine, "fair_prob", {}).get("q_up", 0.50)
                signal = self.engine.inventory.leg_risk_signal(fair_q_up=fair_q_up)
                return {
                    "status": "SUCCESS",
                    "fair_q_up": fair_q_up,
                    "leg_risk": signal,
                }
            return {"status": "ERROR", "error": "Inventory manager not available."}

        return {"error": f"Unknown tool: {name}"}

    async def run_stdio_server(self):
        """Runs the JSON-RPC stdio loop for MCP."""
        while True:
            try:
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                request = json.loads(line.strip())
                req_id = request.get("id")
                method = request.get("method")
                params = request.get("params", {})

                if method == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "serverInfo": {"name": "poly-harvester-mcp", "version": "1.3.0"},
                            "capabilities": {"tools": {}},
                        },
                    }
                elif method == "tools/list":
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"tools": self.get_tool_definitions()},
                    }
                elif method == "tools/call":
                    tool_name = params.get("name")
                    arguments = params.get("arguments", {})
                    result = await self.handle_tool_call(tool_name, arguments)
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
                    }
                else:
                    response = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Method {method} not found"},
                    }

                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
            except Exception as e:
                logger.error(f"MCP Server error: {e}")


if __name__ == "__main__":
    from config import BotConfig
    from main import PolymarketQuantEngine

    config = BotConfig()
    engine = PolymarketQuantEngine(config)
    mcp = PolyHarvesterMCPServer(engine)
    asyncio.run(mcp.run_stdio_server())
