"""
SQLite Database Manager for Poly-Harvester.
Provides transactional, thread-safe persistence for inventory positions,
trade fills, complete-set merges, engine state, user authentication, and sessions.
"""
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("PolyHarvesterDB")


class DatabaseManager:
    """
    Manages SQLite database operations with WAL (Write-Ahead Logging) mode,
    ensuring low latency, crash safety, and complete state recovery across restarts.
    """

    def __init__(self, db_path: str = "data/poly_harvester.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _hash_password(self, password: str, salt: str) -> str:
        """Derives a secure password hash using PBKDF2-HMAC-SHA256."""
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()

    def _init_db(self):
        """Initializes database schema, indexes, and seeded default admin user."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. Active Positions Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS positions (
                        side TEXT PRIMARY KEY,
                        shares REAL NOT NULL DEFAULT 0.0,
                        avg_cost REAL NOT NULL DEFAULT 0.0,
                        total_spent REAL NOT NULL DEFAULT 0.0,
                        updated_at REAL NOT NULL
                    )
                    """
                )

                # 2. Executed Trades Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        time_iso TEXT NOT NULL,
                        order_id TEXT,
                        side TEXT NOT NULL,
                        price REAL NOT NULL,
                        shares REAL NOT NULL,
                        cost_usd REAL NOT NULL,
                        fee_usd REAL NOT NULL DEFAULT 0.0,
                        execution_type TEXT NOT NULL DEFAULT 'PAPER',
                        up_shares_after REAL DEFAULT 0.0,
                        down_shares_after REAL DEFAULT 0.0
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);")

                # 3. Complete Sets Merge Ledger Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS complete_sets (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        time_iso TEXT NOT NULL,
                        sets_merged REAL NOT NULL,
                        up_avg_cost REAL NOT NULL,
                        down_avg_cost REAL NOT NULL,
                        combined_cost REAL NOT NULL,
                        profit_locked REAL NOT NULL,
                        cumulative_pnl REAL NOT NULL
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sets_timestamp ON complete_sets(timestamp);")

                # 4. Engine Key-Value State Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS engine_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )

                # 5. Users Table for Dashboard Authentication
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'admin',
                        created_at REAL NOT NULL,
                        last_login REAL
                    )
                    """
                )

                # 6. Auth Sessions Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS auth_sessions (
                        token TEXT PRIMARY KEY,
                        username TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'admin',
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON auth_sessions(expires_at);")

                # 7. MCP API Keys Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mcp_api_keys (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        key_prefix TEXT NOT NULL,
                        key_hash TEXT UNIQUE NOT NULL,
                        role TEXT NOT NULL DEFAULT 'read_write',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        usage_count INTEGER NOT NULL DEFAULT 0,
                        last_used_at REAL,
                        created_at REAL NOT NULL
                    )
                    """
                )

                # 8. MCP Request / Response Invocation Logs Table
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mcp_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        key_name TEXT,
                        tool_name TEXT NOT NULL,
                        request_args TEXT NOT NULL DEFAULT '{}',
                        response_data TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'SUCCESS',
                        execution_time_ms REAL NOT NULL DEFAULT 0.0,
                        client_ip TEXT NOT NULL DEFAULT '127.0.0.1',
                        created_at REAL NOT NULL
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_mcp_logs_created ON mcp_logs(created_at);")

                # Schema Migration: Add client_ip if table existed previously without it
                try:
                    cursor.execute("ALTER TABLE mcp_logs ADD COLUMN client_ip TEXT NOT NULL DEFAULT '127.0.0.1'")
                except Exception:
                    pass

                # 9. Polymarket Live Credentials & Proxy Settings
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS polymarket_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        private_key TEXT DEFAULT '',
                        wallet_address TEXT DEFAULT '',
                        proxy_url TEXT DEFAULT '',
                        api_key TEXT DEFAULT '',
                        api_secret TEXT DEFAULT '',
                        api_passphrase TEXT DEFAULT '',
                        live_trading_enabled INTEGER DEFAULT 0,
                        updated_at REAL NOT NULL
                    )
                    """
                )

                # 10. Trading Sessions Table for Isolated Session Runs
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trading_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT UNIQUE NOT NULL,
                        name TEXT NOT NULL,
                        mode TEXT NOT NULL DEFAULT 'PAPER',
                        allocated_capital REAL NOT NULL DEFAULT 300.0,
                        order_size_shares REAL NOT NULL DEFAULT 20.0,
                        status TEXT NOT NULL DEFAULT 'ACTIVE',
                        start_time REAL NOT NULL,
                        start_time_iso TEXT NOT NULL,
                        end_time REAL,
                        end_time_iso TEXT,
                        initial_balance REAL DEFAULT 300.0,
                        realized_pnl REAL DEFAULT 0.0,
                        total_trades INTEGER DEFAULT 0,
                        total_volume REAL DEFAULT 0.0,
                        sets_merged REAL DEFAULT 0.0,
                        notes TEXT DEFAULT ''
                    )
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON trading_sessions(status);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_start ON trading_sessions(start_time);")

                # Schema Migrations: Add session_id, market_title, market_slug to trades and complete_sets if absent
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN session_id TEXT NOT NULL DEFAULT 'GLOBAL'")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN market_title TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE trades ADD COLUMN market_slug TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    cursor.execute("ALTER TABLE complete_sets ADD COLUMN session_id TEXT NOT NULL DEFAULT 'GLOBAL'")
                except Exception:
                    pass
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_sets_session ON complete_sets(session_id);")

                # 11. Cloudflare Turnstile Settings
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS turnstile_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        enabled INTEGER NOT NULL DEFAULT 0,
                        site_key TEXT DEFAULT '',
                        secret_key TEXT DEFAULT '',
                        updated_at REAL NOT NULL
                    )
                    """
                )

                # Seed initial position rows if absent
                for side in ("UP", "DOWN"):
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO positions (side, shares, avg_cost, total_spent, updated_at)
                        VALUES (?, 0.0, 0.0, 0.0, ?)
                        """,
                        (side, time.time()),
                    )

                # Seed default admin user if none exists
                cursor.execute("SELECT COUNT(*) FROM users")
                if cursor.fetchone()[0] == 0:
                    salt = secrets.token_hex(16)
                    p_hash = self._hash_password("polyharvester2026", salt)
                    cursor.execute(
                        """
                        INSERT INTO users (username, password_hash, salt, role, created_at)
                        VALUES (?, ?, ?, 'admin', ?)
                        """,
                        ("admin", p_hash, salt, time.time()),
                    )
                    logger.info("Default user 'admin' seeded with default credentials.")

                # Seed default MCP API key if none exists
                cursor.execute("SELECT COUNT(*) FROM mcp_api_keys")
                if cursor.fetchone()[0] == 0:
                    raw_default = "mcp_live_default_agent_key_2026"
                    k_hash = hashlib.sha256(raw_default.encode()).hexdigest()
                    cursor.execute(
                        """
                        INSERT INTO mcp_api_keys (name, key_prefix, key_hash, role, enabled, created_at)
                        VALUES (?, ?, ?, 'read_write', 1, ?)
                        """,
                        ("Default Local Agent", "mcp_live_defau...", k_hash, time.time()),
                    )
                    logger.info("Default MCP API Key seeded: 'Default Local Agent'.")

                conn.commit()
                logger.info(f"Initialized SQLite database at {self.db_path} (WAL Mode enabled).")

    # ================= User & Session Management =================

    def create_user(self, username: str, password: str, role: str = "admin") -> bool:
        """Creates a new user with a hashed password."""
        salt = secrets.token_hex(16)
        p_hash = self._hash_password(password, salt)
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO users (username, password_hash, salt, role, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (username.strip(), p_hash, salt, role, now),
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    return False

    def authenticate_user(self, username: str, password: str) -> Optional[dict]:
        """Validates user credentials against stored PBKDF2 hash."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, username, password_hash, salt, role FROM users WHERE username = ?",
                    (username.strip(),),
                )
                row = cursor.fetchone()
                if not row:
                    return None

                calc_hash = self._hash_password(password, row["salt"])
                if secrets.compare_digest(calc_hash, row["password_hash"]):
                    # Update last_login
                    now = time.time()
                    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))
                    conn.commit()
                    return {
                        "id": row["id"],
                        "username": row["username"],
                        "role": row["role"],
                    }
                return None

    def create_auth_session(self, username: str, role: str = "admin", duration_seconds: int = 86400) -> str:
        """Creates a secure session token with expiration."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires = now + duration_seconds
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO auth_sessions (token, username, role, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (token, username, role, now, expires),
                )
                conn.commit()
        return token

    def validate_session(self, token: str) -> Optional[dict]:
        """Validates an active session token and returns user details."""
        if not token:
            return None
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT username, role, expires_at FROM auth_sessions WHERE token = ? AND expires_at > ?",
                    (token, now),
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "username": row["username"],
                        "role": row["role"],
                        "expires_at": row["expires_at"],
                    }
                return None

    def revoke_session(self, token: str):
        """Revokes a session token on logout."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
                conn.commit()

    def update_user_credentials(
        self,
        current_username: str,
        current_password: str,
        new_username: Optional[str] = None,
        new_password: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Validates current user password, and updates username and/or password.
        Also updates any active auth_sessions with the new username.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, username, password_hash, salt, role FROM users WHERE username = ?",
                    (current_username.strip(),),
                )
                row = cursor.fetchone()
                if not row:
                    return False, "User not found."

                calc_hash = self._hash_password(current_password, row["salt"])
                if not secrets.compare_digest(calc_hash, row["password_hash"]):
                    return False, "Current password is incorrect."

                target_username = current_username.strip()
                if new_username and new_username.strip():
                    target_username = new_username.strip()
                    if len(target_username) < 3:
                        return False, "Username must be at least 3 characters."
                    # Check if another user already has this username
                    cursor.execute(
                        "SELECT id FROM users WHERE username = ? AND id != ?",
                        (target_username, row["id"]),
                    )
                    if cursor.fetchone():
                        return False, f"Username '{target_username}' is already taken."

                new_hash = row["password_hash"]
                new_salt = row["salt"]
                if new_password and new_password.strip():
                    if len(new_password.strip()) < 6:
                        return False, "New password must be at least 6 characters."
                    new_salt = secrets.token_hex(16)
                    new_hash = self._hash_password(new_password.strip(), new_salt)

                conn.execute(
                    """
                    UPDATE users
                    SET username = ?, password_hash = ?, salt = ?
                    WHERE id = ?
                    """,
                    (target_username, new_hash, new_salt, row["id"]),
                )

                if target_username != current_username.strip():
                    conn.execute(
                        "UPDATE auth_sessions SET username = ? WHERE username = ?",
                        (target_username, current_username.strip()),
                    )

                conn.commit()
                return True, target_username

    # ================= Position & Trade Logging =================

    def save_position(self, side: str, shares: float, avg_cost: float, total_spent: float):
        """Atomically saves or updates an active token position."""
        side_upper = side.upper()
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO positions (side, shares, avg_cost, total_spent, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(side) DO UPDATE SET
                        shares = excluded.shares,
                        avg_cost = excluded.avg_cost,
                        total_spent = excluded.total_spent,
                        updated_at = excluded.updated_at
                    """,
                    (side_upper, max(0.0, shares), max(0.0, avg_cost), max(0.0, total_spent), now),
                )
                conn.commit()

    def load_positions(self) -> Dict[str, Dict[str, float]]:
        """Loads active UP and DOWN positions from the database."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT side, shares, avg_cost, total_spent FROM positions")
                rows = cursor.fetchall()
                result = {
                    "UP": {"shares": 0.0, "avg_cost": 0.0, "total_spent": 0.0},
                    "DOWN": {"shares": 0.0, "avg_cost": 0.0, "total_spent": 0.0},
                }
                for r in rows:
                    side = r["side"]
                    if side in result:
                        result[side] = {
                            "shares": float(r["shares"]),
                            "avg_cost": float(r["avg_cost"]),
                            "total_spent": float(r["total_spent"]),
                        }
                return result

    def log_trade(
        self,
        trade_event: dict,
        up_shares_after: float = 0.0,
        down_shares_after: float = 0.0,
        execution_type: str = "PAPER",
        session_id: str = "GLOBAL",
    ) -> int:
        """Appends an executed trade fill to the SQLite database."""
        now = trade_event.get("timestamp", time.time())
        time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        market_title = trade_event.get("market_title", "")
        market_slug = trade_event.get("market_slug", "")
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO trades (
                        timestamp, time_iso, order_id, side, price, shares, cost_usd, fee_usd,
                        execution_type, up_shares_after, down_shares_after, session_id,
                        market_title, market_slug
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        time_iso,
                        trade_event.get("order_id", ""),
                        trade_event.get("side", "").upper(),
                        float(trade_event.get("price", 0.0)),
                        float(trade_event.get("shares", 0.0)),
                        float(trade_event.get("cost", 0.0)),
                        float(trade_event.get("fee", 0.0)),
                        execution_type,
                        float(up_shares_after),
                        float(down_shares_after),
                        session_id,
                        market_title,
                        market_slug,
                    ),
                )
                conn.commit()
                return cursor.lastrowid

    def log_complete_set(
        self,
        sets_merged: float,
        up_avg_cost: float,
        down_avg_cost: float,
        combined_cost: float,
        profit_locked: float,
        cumulative_pnl: float,
        session_id: str = "GLOBAL",
    ) -> int:
        """Appends a complete-set merge and locked profit record."""
        now = time.time()
        time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO complete_sets (
                        timestamp, time_iso, sets_merged, up_avg_cost, down_avg_cost,
                        combined_cost, profit_locked, cumulative_pnl, session_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        time_iso,
                        float(sets_merged),
                        float(up_avg_cost),
                        float(down_avg_cost),
                        float(combined_cost),
                        float(profit_locked),
                        float(cumulative_pnl),
                        session_id,
                    ),
                )
                conn.commit()
                return cursor.lastrowid

    def set_state(self, key: str, value: Any):
        """Saves an arbitrary state value as JSON in the database."""
        now = time.time()
        val_str = json.dumps(value)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO engine_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key, val_str, now),
                )
                conn.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        """Retrieves a state value by key, returning default if not found."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM engine_state WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    try:
                        return json.loads(row["value"])
                    except Exception:
                        return row["value"]
                return default

    def get_recent_trades(self, limit: int = 50, offset: int = 0) -> List[dict]:
        """Retrieves paginated recent trades from SQLite."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, timestamp, time_iso, order_id, side, price, shares, cost_usd,
                           fee_usd, execution_type, up_shares_after, down_shares_after,
                           session_id, market_title, market_slug
                    FROM trades
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def get_complete_sets(self, limit: int = 50, offset: int = 0) -> List[dict]:
        """Retrieves complete set merge records from SQLite."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, timestamp, time_iso, sets_merged, up_avg_cost, down_avg_cost,
                           combined_cost, profit_locked, cumulative_pnl
                    FROM complete_sets
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def get_analytics(self) -> dict:
        """Calculates cumulative performance analytics from SQLite tables."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # Total trades
                cursor.execute("SELECT COUNT(*), COALESCE(SUM(cost_usd), 0.0), COALESCE(SUM(fee_usd), 0.0) FROM trades")
                row_t = cursor.fetchone()
                total_trades = row_t[0]
                total_volume_usd = float(row_t[1])
                total_fees = float(row_t[2])

                # UP trades vs DOWN trades
                cursor.execute("SELECT COUNT(*) FROM trades WHERE UPPER(side) = 'UP'")
                up_trades = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM trades WHERE UPPER(side) = 'DOWN'")
                down_trades = cursor.fetchone()[0]

                # Complete sets
                cursor.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(sets_merged), 0.0), COALESCE(SUM(profit_locked), 0.0),
                           COALESCE(AVG(combined_cost), 0.0)
                    FROM complete_sets
                    """
                )
                row_s = cursor.fetchone()
                total_merge_events = row_s[0]
                total_sets_merged = float(row_s[1])
                total_profit_locked = float(row_s[2])
                avg_combined_cost = float(row_s[3])

                net_pnl = total_profit_locked - total_fees
                profit_margin = ((1.00 - avg_combined_cost) * 100.0) if avg_combined_cost > 0 else 0.0

                return {
                    "total_trades": total_trades,
                    "up_trades_count": up_trades,
                    "down_trades_count": down_trades,
                    "total_volume_usd": round(total_volume_usd, 2),
                    "total_fees_paid": round(total_fees, 2),
                    "total_merge_events": total_merge_events,
                    "total_complete_sets_merged": round(total_sets_merged, 1),
                    "realized_arbitrage_pnl": round(total_profit_locked, 2),
                    "net_pnl": round(net_pnl, 2),
                    "avg_combined_cost": round(avg_combined_cost, 3),
                    "profit_margin_pct": round(profit_margin, 2),
                }

    def reset_positions(self):
        """Resets active position counts to zero."""
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE positions SET shares = 0.0, avg_cost = 0.0, total_spent = 0.0, updated_at = ?",
                    (now,),
                )
                conn.commit()

    # ================= MCP Manager & Logs =================

    def create_mcp_key(self, name: str, role: str = "read_write") -> Tuple[str, dict]:
        """Creates a new MCP API key and returns (raw_key, record_dict)."""
        raw_key = f"mcp_live_{secrets.token_urlsafe(24)}"
        k_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        k_prefix = raw_key[:14] + "..."
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO mcp_api_keys (name, key_prefix, key_hash, role, enabled, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (name.strip() or "Remote Agent", k_prefix, k_hash, role, now),
                )
                key_id = cursor.lastrowid
                conn.commit()
                return raw_key, {
                    "id": key_id,
                    "name": name.strip() or "Remote Agent",
                    "key_prefix": k_prefix,
                    "role": role,
                    "enabled": 1,
                    "usage_count": 0,
                    "last_used_at": None,
                    "created_at": now,
                }

    def list_mcp_keys(self) -> List[dict]:
        """Returns all MCP API keys."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, name, key_prefix, role, enabled, usage_count, last_used_at, created_at
                    FROM mcp_api_keys
                    ORDER BY created_at DESC
                    """
                )
                rows = cursor.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    d["created_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d["created_at"])) if d.get("created_at") else ""
                    d["last_used_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d["last_used_at"])) if d.get("last_used_at") else "Never"
                    result.append(d)
                return result

    def toggle_mcp_key(self, key_id: int, enabled: bool) -> bool:
        """Enables or disables an MCP key."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE mcp_api_keys SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, key_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def delete_mcp_key(self, key_id: int) -> bool:
        """Permanently deletes an MCP API key."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM mcp_api_keys WHERE id = ?", (key_id,))
                conn.commit()
                return cursor.rowcount > 0

    def validate_mcp_key(self, raw_key: str) -> Optional[dict]:
        """Validates an MCP key and increments usage count."""
        if not raw_key:
            return None
        k_hash = hashlib.sha256(raw_key.strip().encode()).hexdigest()
        now = time.time()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, name, role, enabled, usage_count
                    FROM mcp_api_keys
                    WHERE key_hash = ? AND enabled = 1
                    """,
                    (k_hash,),
                )
                row = cursor.fetchone()
                if row:
                    conn.execute(
                        "UPDATE mcp_api_keys SET usage_count = usage_count + 1, last_used_at = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    conn.commit()
                    return dict(row)
                return None

    def log_mcp_call(
        self,
        key_name: str,
        tool_name: str,
        request_args: Any,
        response_data: Any,
        status: str = "SUCCESS",
        execution_time_ms: float = 0.0,
        client_ip: str = "127.0.0.1",
    ):
        """Logs an MCP request/response invocation with client IP."""
        now = time.time()
        req_str = json.dumps(request_args) if isinstance(request_args, (dict, list)) else str(request_args)
        res_str = json.dumps(response_data) if isinstance(response_data, (dict, list)) else str(response_data)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO mcp_logs (key_name, tool_name, request_args, response_data, status, execution_time_ms, client_ip, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key_name, tool_name, req_str, res_str, status, execution_time_ms, client_ip, now),
                )
                conn.commit()

    def get_mcp_logs(self, limit: int = 50, offset: int = 0, tool_filter: Optional[str] = None) -> List[dict]:
        """Returns paginated MCP invocation logs."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if tool_filter:
                    cursor.execute(
                        """
                        SELECT id, key_name, tool_name, request_args, response_data, status, execution_time_ms, client_ip, created_at
                        FROM mcp_logs
                        WHERE tool_name = ?
                        ORDER BY created_at DESC
                        LIMIT ? OFFSET ?
                        """,
                        (tool_filter, limit, offset),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id, key_name, tool_name, request_args, response_data, status, execution_time_ms, client_ip, created_at
                        FROM mcp_logs
                        ORDER BY created_at DESC
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    )
                rows = cursor.fetchall()
                result = []
                for r in rows:
                    d = dict(r)
                    d["created_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d["created_at"]))
                    try:
                        d["request_json"] = json.loads(d["request_args"])
                    except Exception:
                        d["request_json"] = d["request_args"]
                    try:
                        d["response_json"] = json.loads(d["response_data"])
                    except Exception:
                        d["response_json"] = d["response_data"]
                    result.append(d)
                return result

    def get_mcp_stats(self) -> dict:
        """Returns cumulative MCP usage statistics."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), COALESCE(AVG(execution_time_ms), 0.0) FROM mcp_logs")
                row_l = cursor.fetchone()
                total_calls = row_l[0]
                avg_ms = row_l[1]

                cursor.execute("SELECT COUNT(*) FROM mcp_logs WHERE status != 'SUCCESS'")
                error_calls = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM mcp_api_keys WHERE enabled = 1")
                active_keys = cursor.fetchone()[0]

                success_rate = ((total_calls - error_calls) / total_calls * 100.0) if total_calls > 0 else 100.0

                return {
                    "total_calls": total_calls,
                    "error_calls": error_calls,
                    "success_rate_pct": round(success_rate, 2),
                    "avg_latency_ms": round(avg_ms, 2),
                    "active_keys_count": active_keys,
                }

    # =========================================================================
    # POLYMARKET CONFIGURATION & LIVE TRADING
    # =========================================================================

    def save_polymarket_config(
        self,
        private_key: Optional[str] = None,
        wallet_address: Optional[str] = None,
        proxy_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        api_passphrase: Optional[str] = None,
        live_trading_enabled: Optional[bool] = None,
    ) -> dict:
        """Saves or updates runtime Polymarket credentials and proxy configuration."""
        current = self.get_polymarket_config()
        now = time.time()

        pk = private_key if private_key is not None else current.get("private_key", "")
        wa = wallet_address if wallet_address is not None else current.get("wallet_address", "")
        prx = proxy_url if proxy_url is not None else current.get("proxy_url", "")
        ak = api_key if api_key is not None else current.get("api_key", "")
        as_ = api_secret if api_secret is not None else current.get("api_secret", "")
        ap = api_passphrase if api_passphrase is not None else current.get("api_passphrase", "")
        live = int(live_trading_enabled) if live_trading_enabled is not None else int(current.get("live_trading_enabled", 0))

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO polymarket_config (id, private_key, wallet_address, proxy_url, api_key, api_secret, api_passphrase, live_trading_enabled, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        private_key = excluded.private_key,
                        wallet_address = excluded.wallet_address,
                        proxy_url = excluded.proxy_url,
                        api_key = excluded.api_key,
                        api_secret = excluded.api_secret,
                        api_passphrase = excluded.api_passphrase,
                        live_trading_enabled = excluded.live_trading_enabled,
                        updated_at = excluded.updated_at
                    """,
                    (pk, wa, prx, ak, as_, ap, live, now),
                )
                conn.commit()

        return self.get_polymarket_config()

    def get_polymarket_config(self) -> dict:
        """Returns the stored Polymarket credentials and proxy configuration."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT private_key, wallet_address, proxy_url, api_key, api_secret, api_passphrase, live_trading_enabled, updated_at
                    FROM polymarket_config WHERE id = 1
                    """
                )
                row = cursor.fetchone()
                if not row:
                    return {
                        "private_key": "",
                        "wallet_address": "",
                        "proxy_url": "",
                        "api_key": "",
                        "api_secret": "",
                        "api_passphrase": "",
                        "live_trading_enabled": 0,
                        "updated_at": 0.0,
                    }
                return dict(row)

    def save_turnstile_config(
        self,
        enabled: Optional[bool] = None,
        site_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> dict:
        """Saves or updates Cloudflare Turnstile CAPTCHA settings."""
        current = self.get_turnstile_config()
        now = time.time()
        en = int(enabled) if enabled is not None else int(current.get("enabled", 0))
        sk = site_key if site_key is not None else current.get("site_key", "")
        sec = secret_key if secret_key is not None else current.get("secret_key", "")

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO turnstile_config (id, enabled, site_key, secret_key, updated_at)
                    VALUES (1, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        enabled = excluded.enabled,
                        site_key = excluded.site_key,
                        secret_key = excluded.secret_key,
                        updated_at = excluded.updated_at
                    """,
                    (en, sk, sec, now),
                )
                conn.commit()
        return self.get_turnstile_config()

    def get_turnstile_config(self) -> dict:
        """Returns the stored Cloudflare Turnstile configuration."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT enabled, site_key, secret_key, updated_at
                    FROM turnstile_config WHERE id = 1
                    """
                )
                row = cursor.fetchone()
                if not row:
                    return {
                        "enabled": 0,
                        "site_key": "",
                        "secret_key": "",
                        "updated_at": 0.0,
                    }
                return dict(row)

    # =========================================================================
    # TRADING SESSIONS MANAGEMENT (ISOLATED RUNS & CONTROLS)
    # =========================================================================

    def create_session(
        self,
        name: str = "",
        mode: str = "PAPER",
        allocated_capital: float = 300.0,
        order_size_shares: float = 20.0,
        notes: str = "",
    ) -> dict:
        """Creates a new active trading session, stopping any prior active sessions."""
        now = time.time()
        time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        sess_num = int(now) % 100000
        unique_suffix = secrets.token_hex(2).upper()
        sess_id = f"SESS-{time.strftime('%Y%m%d')}-{sess_num:05d}-{unique_suffix}"
        display_name = name.strip() if name.strip() else f"Session #{sess_num:05d} ({mode.upper()})"
        cap = max(10.0, min(300.0, float(allocated_capital)))
        order_sz = max(1.0, min(100.0, float(order_size_shares)))

        with self._lock:
            with self._get_connection() as conn:
                # Stop existing active sessions
                conn.execute(
                    """
                    UPDATE trading_sessions
                    SET status = 'STOPPED', end_time = ?, end_time_iso = ?
                    WHERE status = 'ACTIVE'
                    """,
                    (now, time_iso),
                )
                conn.execute(
                    """
                    INSERT INTO trading_sessions (
                        session_id, name, mode, allocated_capital, order_size_shares,
                        status, start_time, start_time_iso, initial_balance, notes
                    ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)
                    """,
                    (sess_id, display_name, mode.upper(), cap, order_sz, now, time_iso, cap, notes),
                )
                conn.commit()

        return self.get_session_by_id(sess_id)

    def get_active_session(self) -> Optional[dict]:
        """Returns the currently active trading session if one exists."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM trading_sessions
                    WHERE status = 'ACTIVE'
                    ORDER BY id DESC LIMIT 1
                    """
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_session_by_id(self, session_id: str) -> Optional[dict]:
        """Returns session record by session_id."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM trading_sessions WHERE session_id = ?", (session_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def list_sessions(self, limit: int = 50) -> List[dict]:
        """Returns list of historical trading sessions."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM trading_sessions
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                )
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def stop_session(self, session_id: Optional[str] = None) -> Optional[dict]:
        """Stops the current or specified active session and finalizes its stats."""
        now = time.time()
        time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        target_sess = session_id or (self.get_active_session() or {}).get("session_id")
        if not target_sess:
            return None

        analytics = self.get_session_analytics(target_sess)

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE trading_sessions
                    SET status = 'STOPPED', end_time = ?, end_time_iso = ?,
                        realized_pnl = ?, total_trades = ?, total_volume = ?, sets_merged = ?
                    WHERE session_id = ?
                    """,
                    (
                        now,
                        time_iso,
                        analytics.get("realized_arbitrage_pnl", 0.0),
                        analytics.get("total_trades", 0),
                        analytics.get("total_volume_usd", 0.0),
                        analytics.get("total_complete_sets_merged", 0.0),
                        target_sess,
                    ),
                )
                conn.commit()

        return self.get_session_by_id(target_sess)

    def stop_all_active_sessions(self):
        """Archives any lingering active sessions on fresh startup."""
        now = time.time()
        time_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE trading_sessions SET status = 'STOPPED', end_time = ?, end_time_iso = ? WHERE status IN ('ACTIVE', 'PAUSED')",
                    (now, time_iso),
                )
                conn.commit()

    def pause_session(self, session_id: Optional[str] = None) -> Optional[dict]:
        """Sets an active session to PAUSED state."""
        target_sess = session_id or (self.get_active_session() or {}).get("session_id")
        if not target_sess:
            return None
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE trading_sessions SET status = 'PAUSED' WHERE session_id = ?",
                    (target_sess,),
                )
                conn.commit()
        return self.get_session_by_id(target_sess)

    def resume_session(self, session_id: Optional[str] = None) -> Optional[dict]:
        """Resumes a PAUSED session to ACTIVE state."""
        target_sess = session_id or (self.get_active_session() or {}).get("session_id")
        if not target_sess:
            return None
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE trading_sessions SET status = 'ACTIVE' WHERE session_id = ?",
                    (target_sess,),
                )
                conn.commit()
        return self.get_session_by_id(target_sess)

    def get_session_trades_count(self, session_id: Optional[str] = None, market_filter: Optional[str] = None) -> int:
        """Returns total trade count for a session (or aggregate if None/'ALL'), optionally filtered by market."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                conditions = []
                params = []
                if session_id and session_id.upper() != "ALL":
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if market_filter and market_filter.upper() != "ALL":
                    conditions.append("(market_title = ? OR market_slug = ?)")
                    params.extend([market_filter, market_filter])

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                cursor.execute(f"SELECT COUNT(*) FROM trades {where_clause}", tuple(params))
                row = cursor.fetchone()
                return int(row[0]) if row else 0

    def get_session_trades(self, session_id: Optional[str] = None, limit: int = 50, offset: int = 0, market_filter: Optional[str] = None) -> List[dict]:
        """Retrieves trades filtered by session_id (or all if session_id is None/'ALL'), optionally filtered by market."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                conditions = []
                params = []
                if session_id and session_id.upper() != "ALL":
                    conditions.append("session_id = ?")
                    params.append(session_id)
                if market_filter and market_filter.upper() != "ALL":
                    conditions.append("(market_title = ? OR market_slug = ?)")
                    params.extend([market_filter, market_filter])

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                params.extend([limit, offset])
                cursor.execute(
                    f"""
                    SELECT id, timestamp, time_iso, order_id, side, price, shares, cost_usd,
                           fee_usd, execution_type, up_shares_after, down_shares_after, session_id,
                           market_title, market_slug
                    FROM trades
                    {where_clause}
                    ORDER BY id DESC
                    LIMIT ? OFFSET ?
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def get_session_complete_sets_count(self, session_id: Optional[str] = None) -> int:
        """Returns total complete sets merge count for a session (or aggregate if None/'ALL')."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if session_id and session_id.upper() != "ALL":
                    cursor.execute("SELECT COUNT(*) FROM complete_sets WHERE session_id = ?", (session_id,))
                else:
                    cursor.execute("SELECT COUNT(*) FROM complete_sets")
                row = cursor.fetchone()
                return int(row[0]) if row else 0

    def get_session_complete_sets(self, session_id: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[dict]:
        """Retrieves complete sets filtered by session_id."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if session_id and session_id.upper() != "ALL":
                    cursor.execute(
                        """
                        SELECT id, timestamp, time_iso, sets_merged, up_avg_cost, down_avg_cost,
                               combined_cost, profit_locked, cumulative_pnl, session_id
                        FROM complete_sets
                        WHERE session_id = ?
                        ORDER BY id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (session_id, limit, offset),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT id, timestamp, time_iso, sets_merged, up_avg_cost, down_avg_cost,
                               combined_cost, profit_locked, cumulative_pnl, session_id
                        FROM complete_sets
                        ORDER BY id DESC
                        LIMIT ? OFFSET ?
                        """,
                        (limit, offset),
                    )
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

    def get_session_analytics(self, session_id: Optional[str] = None) -> dict:
        """Calculates performance analytics specifically for a session (or aggregate if None/'ALL')."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                filter_clause = "WHERE session_id = ?" if (session_id and session_id.upper() != "ALL") else ""
                params = (session_id,) if (session_id and session_id.upper() != "ALL") else ()

                cursor.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(cost_usd), 0.0), COALESCE(SUM(fee_usd), 0.0) FROM trades {filter_clause}",
                    params,
                )
                row_t = cursor.fetchone()
                total_trades = row_t[0]
                total_volume_usd = float(row_t[1])
                total_fees = float(row_t[2])

                if filter_clause:
                    cursor.execute(
                        "SELECT COUNT(*) FROM trades WHERE UPPER(side) = 'UP' AND session_id = ?",
                        params,
                    )
                else:
                    cursor.execute("SELECT COUNT(*) FROM trades WHERE UPPER(side) = 'UP'")
                up_trades = cursor.fetchone()[0]

                if filter_clause:
                    cursor.execute(
                        "SELECT COUNT(*) FROM trades WHERE UPPER(side) = 'DOWN' AND session_id = ?",
                        params,
                    )
                else:
                    cursor.execute("SELECT COUNT(*) FROM trades WHERE UPPER(side) = 'DOWN'")
                down_trades = cursor.fetchone()[0]

                cursor.execute(
                    f"""
                    SELECT COUNT(*), COALESCE(SUM(sets_merged), 0.0), COALESCE(SUM(profit_locked), 0.0),
                           COALESCE(AVG(combined_cost), 0.0)
                    FROM complete_sets {filter_clause}
                    """,
                    params,
                )
                row_s = cursor.fetchone()
                total_merge_events = row_s[0]
                total_sets_merged = float(row_s[1])
                total_profit_locked = float(row_s[2])
                avg_combined_cost = float(row_s[3])

                net_pnl = total_profit_locked - total_fees
                profit_margin = ((1.00 - avg_combined_cost) * 100.0) if avg_combined_cost > 0 else 0.0

                return {
                    "session_id": session_id or "ALL",
                    "total_trades": total_trades,
                    "up_trades_count": up_trades,
                    "down_trades_count": down_trades,
                    "total_volume_usd": round(total_volume_usd, 2),
                    "total_fees_paid": round(total_fees, 2),
                    "total_merge_events": total_merge_events,
                    "total_complete_sets_merged": round(total_sets_merged, 1),
                    "realized_arbitrage_pnl": round(total_profit_locked, 2),
                    "net_pnl": round(net_pnl, 2),
                    "avg_combined_cost": round(avg_combined_cost, 3),
                    "profit_margin_pct": round(profit_margin, 2),
                }
