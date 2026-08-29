"""
Secure Web Dashboard & WebSocket API Server for Poly-Harvester.
Provides user-authenticated real-time telemetry, SQLite historical analytics,
complete-set merge ledger, and dynamic risk controls with Diskcache state persistence.
"""
import asyncio
import collections
import json
import logging
import os
import secrets
import time
from typing import Optional, Tuple
from aiohttp import web
from mcp_server import PolyHarvesterMCPServer
from models.polymarket_client import PolymarketManager

logger = logging.getLogger("PolyHarvesterDashboard")


class DashboardServer:
    def __init__(self, engine, host: str = "0.0.0.0", port: int = 8443, auth_token: str = "poly-harvester-secure-key-2026"):
        self.engine = engine
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.app = web.Application()
        self.sockets = set()
        self._price_history = collections.deque(maxlen=40)
        self._start_time = time.time()
        self._mcp_server = PolyHarvesterMCPServer(self.engine)
        self._mcp_sse_sessions = {}

        # Initialize Polymarket Manager using config or database settings
        poly_cfg = {}
        if hasattr(self.engine, "db") and self.engine.db:
            try:
                poly_cfg = self.engine.db.get_polymarket_config()
            except Exception:
                poly_cfg = {}

        pk = poly_cfg.get("private_key") or getattr(self.engine.config, "private_key", "")
        wa = poly_cfg.get("wallet_address") or getattr(self.engine.config, "wallet_address", "")
        prx = poly_cfg.get("proxy_url") or getattr(self.engine.config, "proxy_url", "")

        self.poly_manager = PolymarketManager(
            private_key=pk,
            wallet_address=wa,
            proxy_url=prx,
        )
        if poly_cfg.get("live_trading_enabled") == 1:
            self.engine.config.dry_run = False

        self._setup_routes()

    def _extract_token(self, request: web.Request) -> str:
        """Extracts token from headers, Bearer authorization, cookies, or query string."""
        token = request.headers.get("X-Auth-Token")
        if not token:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]
        if not token:
            token = request.cookies.get("poly_session")
        if not token:
            token = request.query.get("token", "")
        return token.strip() if token else ""

    def _extract_mcp_key(self, request: web.Request) -> str:
        """Extracts MCP API Key from headers or query string."""
        key = request.headers.get("X-MCP-API-Key")
        if not key:
            key = request.headers.get("x-api-key")
        if not key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("MCP ") or auth_header.startswith("Bearer "):
                key = auth_header.split(" ")[1]
        if not key:
            key = request.query.get("mcp_key") or request.query.get("api_key") or request.query.get("key") or ""
        return key.strip() if key else ""

    def _verify_auth(self, request: web.Request) -> bool:
        """Verifies session token against SQLite auth_sessions or fallback static token."""
        token = self._extract_token(request)
        if not token:
            return False

        # 1. Validate session token in SQLite DB
        if hasattr(self.engine, "db") and self.engine.db:
            session = self.engine.db.validate_session(token)
            if session:
                return True

        # 2. Validate fallback static token
        return token == self.auth_token

    def _authenticate_mcp_request(self, request: web.Request) -> Tuple[bool, str, Optional[dict]]:
        """Authenticates user session or MCP API Key. Returns (is_authenticated, client_name, key_record)."""
        # 1. Check user session or master auth token
        if self._verify_auth(request):
            return True, "Session User", {"role": "admin", "name": "Session User"}

        # 2. Check MCP API Key in DB
        raw_key = self._extract_mcp_key(request)
        if raw_key and hasattr(self.engine, "db") and self.engine.db:
            record = self.engine.db.validate_mcp_key(raw_key)
            if record:
                return True, record["name"], record

        return False, "Unauthorized", None

    def _get_client_ip(self, request: web.Request) -> str:
        """Extracts client IP address from proxy headers or remote connection."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        if request.remote:
            return request.remote
        peer = request.transport.get_extra_info("peername") if request.transport else None
        if peer:
            return peer[0]
        return "127.0.0.1"

    def _setup_routes(self):
        # Static Assets
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_static("/static/", path=static_dir, name="static")

        # Authentication Endpoints
        self.app.router.add_post("/api/auth/login", self._handle_login)
        self.app.router.add_post("/api/auth/logout", self._handle_logout)
        self.app.router.add_get("/api/auth/me", self._handle_me)
        self.app.router.add_post("/api/auth/update_profile", self._handle_update_profile)

        # Telemetry & Storage Endpoints
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/api/trades", self._handle_trades)
        self.app.router.add_get("/api/complete_sets", self._handle_complete_sets)
        self.app.router.add_get("/api/analytics", self._handle_analytics)

        # Session Management & Isolated Runs
        self.app.router.add_post("/api/sessions/start", self._handle_session_start)
        self.app.router.add_post("/api/sessions/pause", self._handle_session_pause)
        self.app.router.add_post("/api/sessions/resume", self._handle_session_resume)
        self.app.router.add_post("/api/sessions/stop", self._handle_session_stop)
        self.app.router.add_get("/api/sessions/list", self._handle_session_list)
        self.app.router.add_get("/api/sessions/{id}", self._handle_session_details)

        # Dynamic Engine Controls
        self.app.router.add_post("/api/control/pause", self._handle_pause)
        self.app.router.add_post("/api/control/resume", self._handle_resume)
        self.app.router.add_post("/api/control/emergency", self._handle_emergency)
        self.app.router.add_post("/api/control/update_risk", self._handle_update_risk)

        # MCP Manager Endpoints
        self.app.router.add_get("/api/mcp/keys", self._handle_mcp_list_keys)
        self.app.router.add_post("/api/mcp/keys", self._handle_mcp_create_key)
        self.app.router.add_delete("/api/mcp/keys/{id}", self._handle_mcp_delete_key)
        self.app.router.add_post("/api/mcp/keys/{id}/toggle", self._handle_mcp_toggle_key)
        self.app.router.add_get("/api/mcp/logs", self._handle_mcp_get_logs)
        self.app.router.add_get("/api/mcp/stats", self._handle_mcp_get_stats)
        self.app.router.add_post("/api/mcp/execute", self._handle_mcp_execute)
        self.app.router.add_post("/api/mcp/execute/{tool_name}", self._handle_mcp_execute_named)

        # Remote MCP Gateway & Transport Endpoints (SSE, JSON-RPC 2.0, OpenAPI)
        self.app.router.add_get("/mcp/sse", self._handle_mcp_sse)
        self.app.router.add_post("/mcp/messages", self._handle_mcp_messages)
        self.app.router.add_post("/mcp", self._handle_mcp_direct_rpc)
        self.app.router.add_get("/mcp", self._handle_mcp_tools_spec)
        self.app.router.add_get("/mcp/tools.json", self._handle_mcp_tools_spec)
        self.app.router.add_get("/mcp/openapi.json", self._handle_mcp_openapi)

        # Polymarket Live SDK & Geoblock Endpoints
        self.app.router.add_get("/api/polymarket/status", self._handle_polymarket_status)
        self.app.router.add_post("/api/polymarket/config", self._handle_polymarket_config)
        self.app.router.add_post("/api/polymarket/test_connection", self._handle_polymarket_test_connection)

        # Real-time WebSocket
        self.app.router.add_get("/ws", self._handle_websocket)

    async def _handle_index(self, request: web.Request):
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        index_path = os.path.join(static_dir, "index.html")
        return web.FileResponse(index_path)

    async def _handle_login(self, request: web.Request):
        """Authenticates username and password against seeded SQLite users."""
        try:
            body = await request.json()
            username = body.get("username", "").strip()
            password = body.get("password", "")

            # 1. Database-backed authentication
            if hasattr(self.engine, "db") and self.engine.db:
                user = self.engine.db.authenticate_user(username, password)
                if user:
                    token = self.engine.db.create_auth_session(username=user["username"], role=user["role"])
                    response = web.json_response({
                        "success": True,
                        "token": token,
                        "user": {
                            "username": user["username"],
                            "role": user["role"],
                        },
                        "expires_in": 86400,
                    })
                    response.set_cookie("poly_session", token, max_age=86400, httponly=False, samesite="Lax")
                    return response

            # 2. Fallback static token compatibility
            if username == "admin" and (password == self.auth_token or password == "polyharvester2026"):
                token = self.auth_token
                response = web.json_response({
                    "success": True,
                    "token": token,
                    "user": {"username": "admin", "role": "admin"},
                    "expires_in": 86400,
                })
                response.set_cookie("poly_session", token, max_age=86400, httponly=False, samesite="Lax")
                return response

            return web.json_response({"success": False, "error": "Invalid username or password."}, status=401)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)

    async def _handle_logout(self, request: web.Request):
        """Revokes active session."""
        token = self._extract_token(request)
        if token and hasattr(self.engine, "db") and self.engine.db:
            self.engine.db.revoke_session(token)
        response = web.json_response({"success": True, "message": "Logged out successfully."})
        response.del_cookie("poly_session")
        return response

    async def _handle_me(self, request: web.Request):
        """Returns the authenticated user details."""
        token = self._extract_token(request)
        if token and hasattr(self.engine, "db") and self.engine.db:
            session = self.engine.db.validate_session(token)
            if session:
                return web.json_response({"authenticated": True, "user": session})
        if token == self.auth_token:
            return web.json_response({"authenticated": True, "user": {"username": "admin", "role": "admin"}})
        return web.json_response({"authenticated": False, "error": "Not authenticated"}, status=401)

    async def _handle_update_profile(self, request: web.Request):
        """Updates user username and/or password with verification."""
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            body = await request.json()
            current_username = body.get("current_username", "").strip()
            current_password = body.get("current_password", "")
            new_username = body.get("new_username", "").strip() or None
            new_password = body.get("new_password", "").strip() or None

            if not current_username or not current_password:
                return web.json_response({
                    "success": False,
                    "error": "Current username and current password are required."
                }, status=400)

            if hasattr(self.engine, "db") and self.engine.db:
                success, result = self.engine.db.update_user_credentials(
                    current_username=current_username,
                    current_password=current_password,
                    new_username=new_username,
                    new_password=new_password,
                )
                if success:
                    return web.json_response({
                        "success": True,
                        "message": "User credentials updated successfully in SQLite.",
                        "new_username": result,
                    })
                else:
                    return web.json_response({"success": False, "error": result}, status=400)

            return web.json_response({"success": False, "error": "Database not initialized."}, status=500)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)

    async def _handle_status(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        data = self._get_telemetry_payload()
        return web.json_response(data)

    async def _handle_trades(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        limit = int(request.query.get("limit", 50))
        offset = int(request.query.get("offset", 0))
        session_id = request.query.get("session_id")
        if session_id is None and hasattr(self.engine, "current_session_id") and self.engine.current_session_id != "STANDBY":
            session_id = self.engine.current_session_id

        if hasattr(self.engine, "db") and self.engine.db:
            trades = self.engine.db.get_session_trades(session_id=session_id, limit=limit, offset=offset)
        else:
            trades = self.engine.paper_engine.fill_history[-limit:]

        return web.json_response({"trades": trades, "session_id": session_id or "ALL"})

    async def _handle_complete_sets(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        limit = int(request.query.get("limit", 50))
        offset = int(request.query.get("offset", 0))
        session_id = request.query.get("session_id")
        if session_id is None and hasattr(self.engine, "current_session_id") and self.engine.current_session_id != "STANDBY":
            session_id = self.engine.current_session_id

        if hasattr(self.engine, "db") and self.engine.db:
            sets = self.engine.db.get_session_complete_sets(session_id=session_id, limit=limit, offset=offset)
        else:
            sets = []

        return web.json_response({"complete_sets": sets, "session_id": session_id or "ALL"})

    async def _handle_analytics(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        session_id = request.query.get("session_id")
        if session_id is None and hasattr(self.engine, "current_session_id") and self.engine.current_session_id != "STANDBY":
            session_id = self.engine.current_session_id

        if hasattr(self.engine, "db") and self.engine.db:
            analytics = self.engine.db.get_session_analytics(session_id=session_id)
        else:
            inv = self.engine.inventory.get_summary()
            analytics = {
                "total_trades": len(self.engine.paper_engine.fill_history),
                "total_complete_sets_merged": inv["complete_sets_merged"],
                "realized_arbitrage_pnl": inv["realized_arb_pnl"],
                "net_pnl": inv["net_pnl"],
            }

        return web.json_response(analytics)

    # ================= Session Management Handlers =================

    async def _handle_session_start(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json() if request.can_read_body else {}
            name = body.get("name", "").strip()
            mode = body.get("mode", "PAPER").upper()
            allocated_capital = float(body.get("allocated_capital", 300.0))
            order_size_shares = float(body.get("order_size_shares", 20.0))
            notes = body.get("notes", "").strip()

            sess = self.engine.start_session(
                name=name,
                mode=mode,
                allocated_capital=allocated_capital,
                order_size_shares=order_size_shares,
                notes=notes,
            )

            if hasattr(self.engine, "cache") and self.engine.cache:
                self.engine.cache.set_bot_status(is_paused=False, is_stop_loss=False, reason=f"Session started: {sess['session_id']}")

            return web.json_response({
                "status": "SUCCESS",
                "session": sess,
                "message": f"Trading session {sess['session_id']} started successfully.",
            })
        except Exception as e:
            return web.json_response({"status": "ERROR", "error": str(e)}, status=400)

    async def _handle_session_pause(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        sess = self.engine.pause_trading()
        if hasattr(self.engine, "cache") and self.engine.cache:
            self.engine.cache.set_bot_status(is_paused=True, is_stop_loss=False, reason="User paused session")
        return web.json_response({"status": "SUCCESS", "session": sess, "message": "Session paused."})

    async def _handle_session_resume(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        sess = self.engine.resume_trading()
        if hasattr(self.engine, "cache") and self.engine.cache:
            self.engine.cache.set_bot_status(is_paused=False, is_stop_loss=False, reason="User resumed session")
        return web.json_response({"status": "SUCCESS", "session": sess, "message": "Session resumed."})

    async def _handle_session_stop(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        sess = self.engine.stop_session()
        if hasattr(self.engine, "cache") and self.engine.cache:
            self.engine.cache.set_bot_status(is_paused=True, is_stop_loss=False, reason="User stopped session")
        return web.json_response({"status": "SUCCESS", "session": sess, "message": "Session stopped and archived."})

    async def _handle_session_list(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        limit = int(request.query.get("limit", 50))
        sessions = self.engine.db.list_sessions(limit=limit) if hasattr(self.engine, "db") and self.engine.db else []
        active = self.engine.db.get_active_session() if hasattr(self.engine, "db") and self.engine.db else None
        return web.json_response({
            "sessions": sessions,
            "active_session": active,
            "current_session_id": getattr(self.engine, "current_session_id", "STANDBY"),
            "is_trading_active": getattr(self.engine, "is_trading_active", False),
        })

    async def _handle_session_details(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        sess_id = request.match_info.get("id", "")
        sess = self.engine.db.get_session_by_id(sess_id) if hasattr(self.engine, "db") and self.engine.db else None
        analytics = self.engine.db.get_session_analytics(sess_id) if hasattr(self.engine, "db") and self.engine.db else {}
        return web.json_response({"session": sess, "analytics": analytics})

    async def _handle_pause(self, request: web.Request):
        return await self._handle_session_pause(request)

    async def _handle_resume(self, request: web.Request):
        return await self._handle_session_resume(request)

    async def _handle_emergency(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        self.engine.inventory.is_stop_loss_triggered = True
        self.engine.paper_engine.update_quotes(0.0, 0.0, allow_up=False, allow_down=False)

        if hasattr(self.engine, "cache") and self.engine.cache:
            self.engine.cache.set_bot_status(is_paused=True, is_stop_loss=True, reason="EMERGENCY STOP VIA DASHBOARD")

        logger.critical("🚨 Emergency circuit breaker triggered via Web Dashboard!")
        return web.json_response({"success": True, "message": "EMERGENCY SHUTDOWN TRIGGERED."})

    async def _handle_update_risk(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
            updated_cache = {}

            if "order_size_shares" in body:
                val = float(body["order_size_shares"])
                self.engine.config.order_size_shares = val
                self.engine.paper_engine.order_size_shares = val
                updated_cache["order_size_shares"] = val

            if "max_inventory_imbalance" in body:
                val = float(body["max_inventory_imbalance"])
                self.engine.config.max_inventory_imbalance = val
                self.engine.inventory.max_imbalance = val
                self.engine.quoter.max_imbalance = val
                updated_cache["max_inventory_imbalance"] = val

            if "max_combined_cost" in body:
                val = float(body["max_combined_cost"])
                self.engine.config.max_combined_cost = val
                self.engine.quoter.max_combined_cost = val
                self.engine.inventory.max_combined_cost = val
                updated_cache["max_combined_cost"] = val

            if "daily_stop_loss_usd" in body:
                val = float(body["daily_stop_loss_usd"])
                self.engine.config.daily_stop_loss_usd = val
                self.engine.inventory.daily_stop_loss = val
                updated_cache["daily_stop_loss_usd"] = val

            # Persist to diskcache
            if hasattr(self.engine, "cache") and self.engine.cache:
                current_cached = self.engine.cache.get_runtime_config() or {}
                current_cached.update(updated_cache)
                self.engine.cache.set_runtime_config(current_cached)

            if hasattr(self.engine, "db") and self.engine.db:
                self.engine.db.set_state("runtime_config", updated_cache)

            return web.json_response({"success": True, "message": "Risk parameters updated and persisted successfully."})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    # ================= MCP Manager Endpoints =================

    async def _handle_mcp_list_keys(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if hasattr(self.engine, "db") and self.engine.db:
            keys = self.engine.db.list_mcp_keys()
            return web.json_response({"keys": keys})
        return web.json_response({"keys": []})

    async def _handle_mcp_create_key(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
            name = body.get("name", "").strip() or "Remote MCP Agent"
            role = body.get("role", "read_write")
            if hasattr(self.engine, "db") and self.engine.db:
                raw_key, record = self.engine.db.create_mcp_key(name=name, role=role)
                return web.json_response({
                    "success": True,
                    "raw_key": raw_key,
                    "record": record,
                    "message": "MCP API Key created. Save this key now; it will not be displayed again.",
                })
            return web.json_response({"success": False, "error": "Database unavailable"}, status=500)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)

    async def _handle_mcp_delete_key(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            key_id = int(request.match_info["id"])
            if hasattr(self.engine, "db") and self.engine.db:
                ok = self.engine.db.delete_mcp_key(key_id)
                return web.json_response({"success": ok})
            return web.json_response({"success": False, "error": "Database unavailable"}, status=500)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)

    async def _handle_mcp_toggle_key(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            key_id = int(request.match_info["id"])
            body = await request.json()
            enabled = bool(body.get("enabled", True))
            if hasattr(self.engine, "db") and self.engine.db:
                ok = self.engine.db.toggle_mcp_key(key_id, enabled)
                return web.json_response({"success": ok})
            return web.json_response({"success": False, "error": "Database unavailable"}, status=500)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=400)

    async def _handle_mcp_get_logs(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        limit = int(request.query.get("limit", 50))
        offset = int(request.query.get("offset", 0))
        tool_filter = request.query.get("tool")
        if hasattr(self.engine, "db") and self.engine.db:
            logs = self.engine.db.get_mcp_logs(limit=limit, offset=offset, tool_filter=tool_filter)
            return web.json_response({"logs": logs})
        return web.json_response({"logs": []})

    async def _handle_mcp_get_stats(self, request: web.Request):
        if not self._verify_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        if hasattr(self.engine, "db") and self.engine.db:
            stats = self.engine.db.get_mcp_stats()
            return web.json_response(stats)
        return web.json_response({"total_calls": 0, "active_keys_count": 0})

    async def _handle_mcp_execute(self, request: web.Request):
        """Executes an MCP tool call via HTTP API and records invocation logs."""
        key_record = None
        key_name = "Session User"
        if not self._verify_auth(request):
            mcp_raw_key = self._extract_mcp_key(request)
            if mcp_raw_key and hasattr(self.engine, "db") and self.engine.db:
                key_record = self.engine.db.validate_mcp_key(mcp_raw_key)
                if key_record:
                    key_name = key_record["name"]
            if not key_record:
                return web.json_response({"error": "Unauthorized: Invalid or missing MCP API Key"}, status=401)

        try:
            body = await request.json()
            tool_name = body.get("tool") or body.get("name")
            arguments = body.get("arguments") or body.get("params") or {}

            if not tool_name:
                return web.json_response({"error": "Tool name is required."}, status=400)

            # Role check for mutations
            if key_record and key_record.get("role") == "read_only":
                if tool_name in ("poly_emergency_stop", "poly_resume_trading", "poly_update_risk_limits"):
                    return web.json_response({"error": "Forbidden: Read-only API key cannot execute mutation commands."}, status=403)

            t0 = time.time()
            result = await self._mcp_server.handle_tool_call(tool_name, arguments)
            dur_ms = round((time.time() - t0) * 1000.0, 2)
            status = "ERROR" if "error" in result else "SUCCESS"
            client_ip = self._get_client_ip(request)

            if hasattr(self.engine, "db") and self.engine.db:
                self.engine.db.log_mcp_call(
                    key_name=key_name,
                    tool_name=tool_name,
                    request_args=arguments,
                    response_data=result,
                    status=status,
                    execution_time_ms=dur_ms,
                    client_ip=client_ip,
                )

            return web.json_response({
                "status": status,
                "tool": tool_name,
                "execution_ms": dur_ms,
                "client_ip": client_ip,
                "result": result,
            })
        except Exception as e:
            return web.json_response({"status": "ERROR", "error": str(e)}, status=500)

    async def _handle_mcp_execute_named(self, request: web.Request):
        """Allows direct URL calling: POST /api/mcp/execute/poly_get_status."""
        tool_name = request.match_info.get("tool_name", "")
        is_auth, client_name, key_record = self._authenticate_mcp_request(request)
        if not is_auth:
            return web.json_response({"error": "Unauthorized: Valid MCP API Key required."}, status=401)

        try:
            body = {}
            if request.can_read_body:
                try:
                    body = await request.json()
                except Exception:
                    body = {}
            arguments = body.get("arguments") or body

            # Role check for mutations
            if key_record and key_record.get("role") == "read_only":
                if tool_name in ("poly_emergency_stop", "poly_resume_trading", "poly_update_risk_limits"):
                    return web.json_response({"error": "Forbidden: Read-only API key cannot execute mutation commands."}, status=403)

            t0 = time.time()
            result = await self._mcp_server.handle_tool_call(tool_name, arguments)
            dur_ms = round((time.time() - t0) * 1000.0, 2)
            status = "ERROR" if isinstance(result, dict) and "error" in result else "SUCCESS"
            client_ip = self._get_client_ip(request)

            if hasattr(self.engine, "db") and self.engine.db:
                self.engine.db.log_mcp_call(
                    key_name=client_name,
                    tool_name=tool_name,
                    request_args=arguments,
                    response_data=result,
                    status=status,
                    execution_time_ms=dur_ms,
                    client_ip=client_ip,
                )

            return web.json_response({
                "status": status,
                "tool": tool_name,
                "execution_ms": dur_ms,
                "client_ip": client_ip,
                "result": result,
            })
        except Exception as e:
            return web.json_response({"status": "ERROR", "error": str(e)}, status=500)

    async def _process_mcp_jsonrpc(self, request_data: dict, client_name: str, key_record: Optional[dict], client_ip: str = "127.0.0.1") -> dict:
        """Processes standard JSON-RPC 2.0 MCP messages."""
        req_id = request_data.get("id")
        method = request_data.get("method")
        params = request_data.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "poly-harvester-remote-mcp", "version": "1.4.0"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method in ("notifications/initialized", "notifications/cancelled", "ping"):
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": self._mcp_server.get_tool_definitions()},
            }
        elif method == "tools/call":
            tool_name = params.get("name") or params.get("tool")
            arguments = params.get("arguments", {})

            if key_record and key_record.get("role") == "read_only":
                if tool_name in ("poly_emergency_stop", "poly_resume_trading", "poly_update_risk_limits"):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32600, "message": "Forbidden: Read-only API key cannot execute mutation commands."},
                    }

            t0 = time.time()
            result = await self._mcp_server.handle_tool_call(tool_name, arguments)
            dur_ms = round((time.time() - t0) * 1000.0, 2)
            status = "ERROR" if isinstance(result, dict) and "error" in result else "SUCCESS"

            if hasattr(self.engine, "db") and self.engine.db:
                self.engine.db.log_mcp_call(
                    key_name=client_name,
                    tool_name=tool_name,
                    request_args=arguments,
                    response_data=result,
                    status=status,
                    execution_time_ms=dur_ms,
                    client_ip=client_ip,
                )

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
                },
            }
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method '{method}' not found."},
            }

    async def _handle_mcp_sse(self, request: web.Request):
        """Standard MCP Server-Sent Events (SSE) stream transport endpoint."""
        is_auth, client_name, key_record = self._authenticate_mcp_request(request)
        if not is_auth:
            return web.Response(text="Unauthorized: Valid MCP API Key required (e.g. ?api_key=mcp_live_... or header X-MCP-API-Key)", status=401)

        raw_key = self._extract_mcp_key(request)
        client_ip = self._get_client_ip(request)
        session_id = secrets.token_urlsafe(16)

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        self._mcp_sse_sessions[session_id] = {
            "response": response,
            "client_name": client_name,
            "key_record": key_record,
            "client_ip": client_ip,
        }

        # Send initial endpoint event pointing to messages endpoint
        endpoint_uri = f"/mcp/messages?session_id={session_id}&api_key={raw_key}"
        await response.write(f"event: endpoint\ndata: {endpoint_uri}\n\n".encode("utf-8"))

        try:
            while True:
                await asyncio.sleep(15)
                await response.write(b": ping\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            self._mcp_sse_sessions.pop(session_id, None)

        return response

    async def _handle_mcp_messages(self, request: web.Request):
        """Standard MCP messages endpoint for active SSE sessions."""
        session_id = request.query.get("session_id", "")
        session_info = self._mcp_sse_sessions.get(session_id)
        client_ip = self._get_client_ip(request)

        if session_info:
            client_name = session_info["client_name"]
            key_record = session_info["key_record"]
            client_ip = session_info.get("client_ip") or client_ip
        else:
            is_auth, client_name, key_record = self._authenticate_mcp_request(request)
            if not is_auth:
                return web.json_response({"jsonrpc": "2.0", "error": {"code": -32600, "message": "Unauthorized"}}, status=401)

        try:
            req_data = await request.json()
            res_data = await self._process_mcp_jsonrpc(req_data, client_name, key_record, client_ip=client_ip)

            # Send message event to SSE stream
            if session_info and session_info.get("response"):
                try:
                    sse_msg = f"event: message\ndata: {json.dumps(res_data)}\n\n"
                    await session_info["response"].write(sse_msg.encode("utf-8"))
                except Exception:
                    pass

            return web.json_response(res_data)
        except Exception as e:
            return web.json_response({"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {e}"}}, status=400)

    async def _handle_mcp_direct_rpc(self, request: web.Request):
        """Direct HTTP JSON-RPC 2.0 endpoint for MCP without requiring persistent SSE."""
        is_auth, client_name, key_record = self._authenticate_mcp_request(request)
        if not is_auth:
            return web.json_response({
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Unauthorized: Valid MCP API Key required via header X-MCP-API-Key or ?api_key=..."},
            }, status=401)

        client_ip = self._get_client_ip(request)
        try:
            req_data = await request.json()
            res_data = await self._process_mcp_jsonrpc(req_data, client_name, key_record, client_ip=client_ip)
            return web.json_response(res_data)
        except Exception as e:
            return web.json_response({"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {e}"}}, status=400)

    async def _handle_mcp_tools_spec(self, request: web.Request):
        """Returns tool schema in JSON for AI agent integration."""
        tools = self._mcp_server.get_tool_definitions()
        return web.json_response({
            "name": "poly-harvester-mcp",
            "version": "1.4.0",
            "description": "Polymarket Quantitative Arbitrage & Market Making MCP API",
            "tools": tools,
        })

    async def _handle_mcp_openapi(self, request: web.Request):
        """Returns standard OpenAPI 3.0.0 JSON specification for Custom GPTs / OpenAI Actions."""
        host = request.host
        scheme = request.scheme
        tools = self._mcp_server.get_tool_definitions()

        paths = {}
        for t in tools:
            name = t["name"]
            desc = t["description"]
            props = t["inputSchema"].get("properties", {})
            paths[f"/api/mcp/execute/{name}"] = {
                "post": {
                    "summary": name,
                    "description": desc,
                    "operationId": name,
                    "requestBody": {
                        "required": bool(props),
                        "content": {
                            "application/json": {
                                "schema": t["inputSchema"]
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Successful tool execution",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            }
                        }
                    }
                }
            }

        openapi_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Poly-Harvester Quant Engine API",
                "version": "1.4.0",
                "description": "Remote MCP & Tool Calling API for Polymarket arbitrage, live feeds, positions, and risk overrides.",
            },
            "servers": [{"url": f"{scheme}://{host}"}],
            "paths": paths,
            "components": {
                "securitySchemes": {
                    "ApiKeyAuth": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-MCP-API-Key"
                    }
                }
            },
            "security": [{"ApiKeyAuth": []}]
        }
        return web.json_response(openapi_spec)

    async def _handle_websocket(self, request: web.Request):
        if not self._verify_auth(request):
            return web.Response(text="Unauthorized", status=401)

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.sockets.add(ws)

        try:
            while not ws.closed:
                payload = self._get_telemetry_payload()
                if hasattr(self.engine, "cache") and self.engine.cache:
                    self.engine.cache.set_telemetry(payload)
                await ws.send_json(payload)
                await asyncio.sleep(0.5)
        finally:
            self.sockets.remove(ws)

        return ws

    def _get_telemetry_payload(self) -> dict:
        inv = self.engine.inventory.get_summary()
        quotes = self.engine.current_quotes
        bn_price = self.engine.binance_feed.current_price
        bn_vel = self.engine.binance_feed.get_velocity()

        if bn_price > 0:
            self._price_history.append({
                "time": time.time(),
                "price": bn_price,
                "velocity": bn_vel
            })

        active_sess = self.engine.db.get_active_session() if hasattr(self.engine, "db") and self.engine.db else None
        current_sess_id = getattr(self.engine, "current_session_id", "STANDBY")
        is_trading = getattr(self.engine, "is_trading_active", False)

        sess_analytics = {}
        if hasattr(self.engine, "db") and self.engine.db:
            sess_analytics = self.engine.db.get_session_analytics(current_sess_id if current_sess_id != "STANDBY" else None)
        else:
            sess_analytics = {
                "total_trades": len(self.engine.paper_engine.fill_history),
                "total_complete_sets_merged": inv["complete_sets_merged"],
                "realized_arbitrage_pnl": inv["realized_arb_pnl"],
                "net_pnl": inv["net_pnl"],
            }

        return {
            "timestamp": time.time(),
            "uptime_seconds": int(time.time() - self._start_time),
            "session": {
                "session_id": current_sess_id,
                "is_trading_active": is_trading,
                "name": active_sess.get("name") if active_sess else "Standby (Not Trading)",
                "mode": active_sess.get("mode", "PAPER") if active_sess else ("PAPER" if self.engine.config.dry_run else "LIVE"),
                "allocated_capital": getattr(self.engine.inventory, "allocated_capital", 300.0),
                "order_size_shares": self.engine.config.order_size_shares,
                "start_time": active_sess.get("start_time") if active_sess else None,
                "status": "ACTIVE" if is_trading else ("PAUSED" if active_sess else "STANDBY"),
            },
            "binance": {
                "symbol": self.engine.config.binance_symbol,
                "price": bn_price,
                "velocity": round(bn_vel, 2),
                "return_10s_pct": round(self.engine.binance_feed.get_percent_return() * 100.0, 3),
                "history": list(self._price_history),
            },
            "bayesian": {
                "q_up": self.engine.fair_prob.get("q_up", 0.50),
                "q_down": self.engine.fair_prob.get("q_down", 0.50),
                "momentum": self.engine.fair_prob.get("momentum_score", 0.0),
            },
            "polymarket": {
                "market_title": self.engine.polymarket_feed.market_title,
                "up_bid": self.engine.polymarket_feed.up_best_bid,
                "up_ask": self.engine.polymarket_feed.up_best_ask,
                "down_bid": self.engine.polymarket_feed.down_best_bid,
                "down_ask": self.engine.polymarket_feed.down_best_ask,
                "sdk_telemetry": self.poly_manager.get_telemetry(),
            },
            "live_trading_active": not self.engine.config.dry_run,
            "quotes": {
                "quote_up": quotes.get("quote_up", 0.0),
                "quote_down": quotes.get("quote_down", 0.0),
                "combined_cost": quotes.get("projected_cost", 0.0),
                "edge_pct": round(quotes.get("projected_edge", 0.0) * 100.0, 2),
                "max_cost_limit": self.engine.config.max_combined_cost,
            },
            "inventory": inv,
            "analytics": sess_analytics,
            "latency": {
                "binance_ms": getattr(self.engine.binance_feed, "latency_ms", 24),
                "polymarket_ms": getattr(self.engine.polymarket_feed, "latency_ms", 38),
            },
            "risk": {
                "order_size_shares": self.engine.config.order_size_shares,
                "max_inventory_imbalance": self.engine.config.max_inventory_imbalance,
                "daily_stop_loss_usd": self.engine.config.daily_stop_loss_usd,
                "is_stop_loss_triggered": inv["is_stop_loss_triggered"],
            },
        }

    # =========================================================================
    # POLYMARKET LIVE SDK & GEOBLOCK HANDLERS
    # =========================================================================

    async def _handle_polymarket_status(self, request: web.Request):
        if not self._verify_auth(request):
            return web.Response(text="Unauthorized", status=401)

        telemetry = self.poly_manager.get_telemetry()
        cfg = {}
        if hasattr(self.engine, "db") and self.engine.db:
            cfg = self.engine.db.get_polymarket_config()
            if cfg.get("private_key"):
                pk = cfg["private_key"]
                cfg["private_key_masked"] = pk[:6] + "..." + pk[-4:] if len(pk) > 10 else "***"
                cfg["private_key"] = ""
            if cfg.get("api_secret"):
                cfg["api_secret"] = "***"

        return web.json_response({
            "status": "SUCCESS",
            "telemetry": telemetry,
            "config": cfg,
            "live_trading_active": not self.engine.config.dry_run,
        })

    async def _handle_polymarket_config(self, request: web.Request):
        if not self._verify_auth(request):
            return web.Response(text="Unauthorized", status=401)

        try:
            body = await request.json()
            pk = body.get("private_key")
            wa = body.get("wallet_address")
            prx = body.get("proxy_url")
            ak = body.get("api_key")
            as_ = body.get("api_secret")
            ap = body.get("api_passphrase")
            live = body.get("live_trading_enabled")

            # Clean and auto-derive wallet address from private key if needed
            if pk:
                pk = pk.strip()
                if not wa or len(wa.strip()) < 40:
                    try:
                        from eth_account import Account
                        wa = Account.from_key(pk).address
                    except Exception:
                        pass
            if wa:
                wa = wa.strip()

            if hasattr(self.engine, "db") and self.engine.db:
                self.engine.db.save_polymarket_config(
                    private_key=pk if pk else None,
                    wallet_address=wa if wa else None,
                    proxy_url=prx if prx is not None else None,
                    api_key=ak if ak else None,
                    api_secret=as_ if as_ else None,
                    api_passphrase=ap if ap else None,
                    live_trading_enabled=live,
                )

            # Update live engine and manager runtime credentials
            self.poly_manager.update_credentials(
                private_key=pk if pk else None,
                wallet_address=wa if wa else None,
                proxy_url=prx if prx is not None else None,
            )

            if live is not None:
                self.engine.config.dry_run = not bool(live)

            # Re-initialize SDK connections
            await self.poly_manager.initialize()

            return web.json_response({
                "status": "SUCCESS",
                "message": "Polymarket settings updated successfully.",
                "telemetry": self.poly_manager.get_telemetry(),
                "live_trading_active": not self.engine.config.dry_run,
            })
        except Exception as e:
            logger.error(f"Error saving Polymarket config: {e}")
            return web.json_response({"status": "ERROR", "error": str(e)}, status=500)

    async def _handle_polymarket_test_connection(self, request: web.Request):
        if not self._verify_auth(request):
            return web.Response(text="Unauthorized", status=401)

        try:
            body = await request.json() if request.can_read_body else {}
            proxy_url = body.get("proxy_url")

            geo = await self.poly_manager.geoblock_checker.check_geoblock(
                proxy_url=proxy_url if proxy_url is not None else self.poly_manager.proxy_url
            )
            bal = await self.poly_manager.refresh_balance()

            return web.json_response({
                "status": "SUCCESS",
                "geoblock": geo,
                "balance": bal,
                "is_authenticated": bool(self.poly_manager._secure_client),
            })
        except Exception as e:
            return web.json_response({"status": "ERROR", "error": str(e)}, status=500)

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        logger.info(f"🚀 Secure Web Dashboard running at http://{self.host}:{self.port}/")
