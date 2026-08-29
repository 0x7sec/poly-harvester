"""
Trade and Performance Event Recorder.
"""
import csv
import os
import time
from typing import Dict, List


class TradeRecorder:
    """
    Logs simulated and live trades, inventory metrics, and PnL to CSV for performance analysis.
    """

    def __init__(self, filepath: str = "trades_simulated.csv"):
        self.filepath = filepath
        self._initialize_csv()

    def _initialize_csv(self):
        if not os.path.exists(self.filepath):
            with open(self.filepath, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "timestamp",
                        "time_iso",
                        "side",
                        "price",
                        "shares",
                        "cost_usd",
                        "up_shares_held",
                        "down_shares_held",
                        "complete_sets_merged",
                        "realized_arb_pnl",
                        "net_pnl",
                    ]
                )

    def log_trade(self, trade_event: dict, inventory_summary: dict):
        """Appends trade and inventory snapshot to CSV."""
        now = time.time()
        time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

        with open(self.filepath, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    now,
                    time_iso,
                    trade_event.get("side", ""),
                    trade_event.get("price", 0.0),
                    trade_event.get("shares", 0.0),
                    trade_event.get("cost", 0.0),
                    inventory_summary.get("up_shares", 0.0),
                    inventory_summary.get("down_shares", 0.0),
                    inventory_summary.get("complete_sets_merged", 0.0),
                    inventory_summary.get("realized_arb_pnl", 0.0),
                    inventory_summary.get("net_pnl", 0.0),
                ]
            )
