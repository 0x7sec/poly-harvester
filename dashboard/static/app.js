/**
 * Poly-Harvester Cyber-Quant Web Dashboard Client
 * Real-time WebSocket streaming, Canvas sparkline charting, SQLite logs, Lucide icons, and custom confirmation modals.
 */

let authToken = localStorage.getItem("poly_token") || "";
let ws = null;
let reconnectTimer = null;
let lastPingTime = 0;
let latency = 0;
let lastPrice = 0;
let priceHistory = [];

// DOM Elements
const authModal = document.getElementById("authModal");
const loginForm = document.getElementById("loginForm");
const loginUsername = document.getElementById("loginUsername");
const loginPassword = document.getElementById("loginPassword");
const loginErrorMsg = document.getElementById("loginErrorMsg");
const btnAutofillAdmin = document.getElementById("btnAutofillAdmin");
const currentUserDisplay = document.getElementById("currentUserDisplay");
const btnLogout = document.getElementById("btnLogout");
const btnOpenProfileModal = document.getElementById("btnOpenProfileModal");
const profileModal = document.getElementById("profileModal");
const btnCloseProfileModal = document.getElementById("btnCloseProfileModal");
const btnCancelProfile = document.getElementById("btnCancelProfile");
const profileSettingsForm = document.getElementById("profileSettingsForm");
const profCurrentUsername = document.getElementById("profCurrentUsername");
const profCurrentPassword = document.getElementById("profCurrentPassword");
const profNewUsername = document.getElementById("profNewUsername");
const profNewPassword = document.getElementById("profNewPassword");
const profConfirmPassword = document.getElementById("profConfirmPassword");
const profileErrorMsg = document.getElementById("profileErrorMsg");

const wsStatus = document.getElementById("wsStatus");
const wsStatusText = document.getElementById("wsStatusText");
const latencyValue = document.getElementById("latencyValue");
const binancePingVal = document.getElementById("binancePingVal");
const polyPingVal = document.getElementById("polyPingVal");
const statusPulse = document.getElementById("statusPulse");

const bnPrice = document.getElementById("bnPrice");
const bnVelocity = document.getElementById("bnVelocity");
const bnReturn = document.getElementById("bnReturn");
const bnSymbol = document.getElementById("bnSymbol");
const priceSparkline = document.getElementById("priceSparkline");

const probUpLabel = document.getElementById("probUpLabel");
const probDnLabel = document.getElementById("probDnLabel");
const probBarUp = document.getElementById("probBarUp");
const probBarDn = document.getElementById("probBarDn");
const momScore = document.getElementById("momScore");
const clobSpread = document.getElementById("clobSpread");

const quoteUp = document.getElementById("quoteUp");
const quoteDn = document.getElementById("quoteDn");
const quoteSum = document.getElementById("quoteSum");
const edgeBadge = document.getElementById("edgeBadge");
const maxCostCap = document.getElementById("maxCostCap");

const invUpShares = document.getElementById("invUpShares");
const invUpCost = document.getElementById("invUpCost");
const invDnShares = document.getElementById("invDnShares");
const invDnCost = document.getElementById("invDnCost");
const imbalanceBadge = document.getElementById("imbalanceBadge");
const setsMerged = document.getElementById("setsMerged");

const realizedPnl = document.getElementById("realizedPnl");
const pnlStatus = document.getElementById("pnlStatus");
const stopLossCap = document.getElementById("stopLossCap");
const capitalUsage = document.getElementById("capitalUsage");

const btnResume = document.getElementById("btnResume");
const btnPause = document.getElementById("btnPause");
const btnEmergency = document.getElementById("btnEmergency");
const riskForm = document.getElementById("riskForm");
const inputOrderSize = document.getElementById("inputOrderSize");
const inputMaxImbalance = document.getElementById("inputMaxImbalance");
const inputMaxCost = document.getElementById("inputMaxCost");

const tabBtnTrades = document.getElementById("tabBtnTrades");
const tabBtnSets = document.getElementById("tabBtnSets");
const tabBtnAnalytics = document.getElementById("tabBtnAnalytics");
const tabBtnMCP = document.getElementById("tabBtnMCP");
const sectionTrades = document.getElementById("sectionTrades");
const sectionSets = document.getElementById("sectionSets");
const sectionAnalytics = document.getElementById("sectionAnalytics");
const sectionMCP = document.getElementById("sectionMCP");
const tradeBody = document.getElementById("tradeBody");
const setsBody = document.getElementById("setsBody");

// Analytics elements
const statTotalTrades = document.getElementById("statTotalTrades");
const statTradeSides = document.getElementById("statTradeSides");
const statTotalVolume = document.getElementById("statTotalVolume");
const statTotalSets = document.getElementById("statTotalSets");
const statAvgCost = document.getElementById("statAvgCost");
const statProfitMargin = document.getElementById("statProfitMargin");
const statTotalFees = document.getElementById("statTotalFees");

// MCP Manager Elements
const mcpStatActiveKeys = document.getElementById("mcpStatActiveKeys");
const mcpStatTotalCalls = document.getElementById("mcpStatTotalCalls");
const mcpStatAvgLatency = document.getElementById("mcpStatAvgLatency");
const mcpStatSuccessRate = document.getElementById("mcpStatSuccessRate");
const mcpStatErrors = document.getElementById("mcpStatErrors");
const mcpKeysBody = document.getElementById("mcpKeysBody");
const mcpLogsBody = document.getElementById("mcpLogsBody");
const mcpToolFilter = document.getElementById("mcpToolFilter");
const btnRefreshMCP = document.getElementById("btnRefreshMCP");
const btnOpenCreateKeyModal = document.getElementById("btnOpenCreateKeyModal");

// MCP Create Key Modal
const mcpCreateKeyModal = document.getElementById("mcpCreateKeyModal");
const btnCloseMcpKeyModal = document.getElementById("btnCloseMcpKeyModal");
const btnCancelMcpKey = document.getElementById("btnCancelMcpKey");
const mcpKeyForm = document.getElementById("mcpKeyForm");
const mcpKeyName = document.getElementById("mcpKeyName");
const mcpKeyRole = document.getElementById("mcpKeyRole");
const mcpKeyErrorMsg = document.getElementById("mcpKeyErrorMsg");
const generatedKeyBox = document.getElementById("generatedKeyBox");
const generatedKeyRaw = document.getElementById("generatedKeyRaw");
const btnCopyGeneratedKey = document.getElementById("btnCopyGeneratedKey");
const mcpKeyActions = document.getElementById("mcpKeyActions");

// MCP Payload Inspector Modal
const mcpPayloadModal = document.getElementById("mcpPayloadModal");
const mcpPayloadModalTitle = document.getElementById("mcpPayloadModalTitle");
const mcpPayloadModalSubtitle = document.getElementById("mcpPayloadModalSubtitle");
const mcpReqArgsJson = document.getElementById("mcpReqArgsJson");
const mcpResDataJson = document.getElementById("mcpResDataJson");
const btnClosePayloadModal = document.getElementById("btnClosePayloadModal");
const btnClosePayloadModalBtn = document.getElementById("btnClosePayloadModalBtn");
const btnCopyReqArgs = document.getElementById("btnCopyReqArgs");
const btnCopyResData = document.getElementById("btnCopyResData");

// Custom Confirmation Modal Elements
const customConfirmModal = document.getElementById("customConfirmModal");
const confirmDialogIcon = document.getElementById("confirmDialogIcon");
const confirmDialogIconWrapper = document.getElementById("confirmDialogIconWrapper");
const confirmDialogTitle = document.getElementById("confirmDialogTitle");
const confirmDialogMessage = document.getElementById("confirmDialogMessage");
const btnConfirmCancel = document.getElementById("btnConfirmCancel");
const btnConfirmAccept = document.getElementById("btnConfirmAccept");

// ==================== Lucide Icons Helper ====================

function refreshIcons() {
    if (window.lucide && typeof lucide.createIcons === 'function') {
        lucide.createIcons();
    }
}

// ==================== Custom Confirmation Modal (Replaces Browser Dialogs) ====================

function customConfirm({
    title = "Confirm Action",
    message = "Are you sure you want to proceed?",
    confirmText = "Confirm",
    cancelText = "Cancel",
    type = "info", // "emergency", "warning", "info"
    icon = "alert-triangle"
}) {
    return new Promise((resolve) => {
        confirmDialogTitle.textContent = title;
        confirmDialogMessage.textContent = message;
        btnConfirmAccept.textContent = confirmText;
        btnConfirmCancel.textContent = cancelText;

        // Reset classes
        confirmDialogIconWrapper.className = "modal-dialog-icon-wrapper";
        btnConfirmAccept.className = "btn";

        if (type === "emergency") {
            confirmDialogIconWrapper.classList.add("emergency");
            btnConfirmAccept.classList.add("btn-emergency");
            confirmDialogIcon.setAttribute("data-lucide", icon || "shield-alert");
        } else if (type === "warning") {
            confirmDialogIconWrapper.classList.add("warning");
            btnConfirmAccept.classList.add("btn-primary");
            confirmDialogIcon.setAttribute("data-lucide", icon || "alert-triangle");
        } else {
            confirmDialogIconWrapper.classList.add("info");
            btnConfirmAccept.classList.add("btn-primary");
            confirmDialogIcon.setAttribute("data-lucide", icon || "info");
        }

        refreshIcons();
        customConfirmModal.classList.remove("hidden");

        const cleanup = () => {
            customConfirmModal.classList.add("hidden");
            btnConfirmAccept.removeEventListener("click", onAccept);
            btnConfirmCancel.removeEventListener("click", onCancel);
        };

        const onAccept = () => {
            cleanup();
            resolve(true);
        };

        const onCancel = () => {
            cleanup();
            resolve(false);
        };

        btnConfirmAccept.addEventListener("click", onAccept);
        btnConfirmCancel.addEventListener("click", onCancel);
    });
}

// ==================== Custom Animated Toast Notifications ====================

function showToast(message, type = "success") {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    
    let iconName = "check-circle-2";
    if (type === "warning") iconName = "alert-triangle";
    if (type === "error") iconName = "alert-octagon";

    toast.innerHTML = `
        <i data-lucide="${iconName}" class="icon-sm"></i>
        <span>${message}</span>
    `;
    container.appendChild(toast);
    refreshIcons();

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(100%)";
        setTimeout(() => toast.remove(), 300);
    }, 3800);
}

// ==================== Authentication Flow ====================

btnAutofillAdmin.addEventListener("click", () => {
    loginUsername.value = "admin";
    loginPassword.value = "polyharvester2026";
    loginErrorMsg.classList.add("hidden");
    showToast("Default credentials filled", "success");
});

async function checkExistingAuth() {
    if (!authToken) {
        showLoginModal();
        return;
    }
    try {
        const res = await fetch("/api/auth/me", {
            headers: { "X-Auth-Token": authToken }
        });
        if (res.ok) {
            const data = await res.json();
            currentUserDisplay.textContent = (data.user && data.user.username) ? data.user.username.toUpperCase() : "ADMIN";
            hideLoginModal();
            connectWebSocket();
            loadHistoricalLogs();
        } else {
            showLoginModal();
        }
    } catch (e) {
        showLoginModal();
    }
}

function showLoginModal() {
    authModal.classList.remove("hidden");
    refreshIcons();
}

function hideLoginModal() {
    authModal.classList.add("hidden");
    loginErrorMsg.classList.add("hidden");
}

loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const u = loginUsername.value.trim();
    const p = loginPassword.value;
    loginErrorMsg.classList.add("hidden");

    try {
        const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: u, password: p })
        });
        const data = await res.json();

        if (res.ok && data.success) {
            authToken = data.token;
            localStorage.setItem("poly_token", authToken);
            currentUserDisplay.textContent = (data.user && data.user.username) ? data.user.username.toUpperCase() : u.toUpperCase();
            hideLoginModal();
            showToast("Authenticated. Connecting live telemetry...", "success");
            connectWebSocket();
            loadHistoricalLogs();
        } else {
            loginErrorMsg.textContent = data.error || "Authentication failed.";
            loginErrorMsg.classList.remove("hidden");
        }
    } catch (err) {
        loginErrorMsg.textContent = "Unable to connect to authentication server.";
        loginErrorMsg.classList.remove("hidden");
    }
});

btnLogout.addEventListener("click", async () => {
    const accepted = await customConfirm({
        title: "Log Out of Terminal",
        message: "Are you sure you want to end your active session on the Quant Control Center?",
        type: "warning",
        confirmText: "Log Out",
        cancelText: "Stay Connected",
        icon: "log-out"
    });

    if (accepted) {
        try {
            await fetch("/api/auth/logout", {
                method: "POST",
                headers: { "X-Auth-Token": authToken }
            });
        } catch (e) {}
        authToken = "";
        localStorage.removeItem("poly_token");
        if (ws) ws.close();
        currentUserDisplay.textContent = "GUEST";
        showToast("Logged out successfully.", "warning");
        showLoginModal();
    }
});

// ==================== Profile & Security Settings ====================

function showProfileModal() {
    if (!profileModal) return;
    profCurrentUsername.value = currentUserDisplay.textContent.toLowerCase();
    profCurrentPassword.value = "";
    profNewUsername.value = "";
    profNewPassword.value = "";
    profConfirmPassword.value = "";
    profileErrorMsg.classList.add("hidden");
    profileModal.classList.remove("hidden");
    refreshIcons();
}

function hideProfileModal() {
    if (profileModal) profileModal.classList.add("hidden");
}

if (btnOpenProfileModal) {
    btnOpenProfileModal.addEventListener("click", showProfileModal);
}
if (btnCloseProfileModal) {
    btnCloseProfileModal.addEventListener("click", hideProfileModal);
}
if (btnCancelProfile) {
    btnCancelProfile.addEventListener("click", hideProfileModal);
}

if (profileSettingsForm) {
    profileSettingsForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        profileErrorMsg.classList.add("hidden");

        const curU = profCurrentUsername.value.trim();
        const curP = profCurrentPassword.value;
        const newU = profNewUsername.value.trim();
        const newP = profNewPassword.value;
        const confP = profConfirmPassword.value;

        if (newP && newP !== confP) {
            profileErrorMsg.textContent = "New password and confirmation do not match.";
            profileErrorMsg.classList.remove("hidden");
            return;
        }

        if (newP && newP.length < 6) {
            profileErrorMsg.textContent = "New password must be at least 6 characters.";
            profileErrorMsg.classList.remove("hidden");
            return;
        }

        try {
            const res = await fetch("/api/auth/update_profile", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Auth-Token": authToken
                },
                body: JSON.stringify({
                    current_username: curU,
                    current_password: curP,
                    new_username: newU || undefined,
                    new_password: newP || undefined,
                })
            });

            const data = await res.json();
            if (res.ok && data.success) {
                if (data.new_username) {
                    currentUserDisplay.textContent = data.new_username.toUpperCase();
                }
                hideProfileModal();
                showToast("Credentials updated successfully.", "success");
            } else {
                profileErrorMsg.textContent = data.error || "Failed to update credentials.";
                profileErrorMsg.classList.remove("hidden");
            }
        } catch (err) {
            profileErrorMsg.textContent = "Network error updating credentials.";
            profileErrorMsg.classList.remove("hidden");
        }
    });
}

// ==================== WebSocket Telemetry Stream ====================

function connectWebSocket() {
    if (ws) {
        try { ws.close(); } catch (e) {}
    }
    if (!authToken) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(authToken)}`;

    wsStatusText.textContent = "CONNECTING...";
    wsStatus.style.borderColor = "rgba(245, 158, 11, 0.4)";
    wsStatus.style.color = "var(--accent-yellow)";

    lastPingTime = Date.now();
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        wsStatusText.textContent = "STREAM ACTIVE";
        wsStatus.style.borderColor = "rgba(16, 185, 129, 0.4)";
        wsStatus.style.color = "var(--accent-green)";
        statusPulse.style.backgroundColor = "var(--accent-green)";
        if (reconnectTimer) clearTimeout(reconnectTimer);
        refreshIcons();
    };

    ws.onmessage = (event) => {
        try {
            latency = Date.now() - lastPingTime;
            lastPingTime = Date.now();
            latencyValue.textContent = `${latency} ms`;

            const data = JSON.parse(event.data);
            updateDashboard(data);
        } catch (err) {
            console.error("Telemetry parse error:", err);
        }
    };

    ws.onclose = () => {
        wsStatusText.textContent = "DISCONNECTED";
        wsStatus.style.borderColor = "rgba(239, 68, 68, 0.4)";
        wsStatus.style.color = "var(--accent-red)";
        statusPulse.style.backgroundColor = "var(--accent-red)";
        reconnectTimer = setTimeout(connectWebSocket, 3000);
    };

    ws.onerror = () => {
        if (ws) ws.close();
    };
}

// ==================== Dashboard Metrics Update ====================

function updateDashboard(data) {
    // 1. Reference Feed (Binance)
    if (data.binance) {
        const p = data.binance.price || 0;
        const v = data.binance.velocity || 0;
        const r = data.binance.return_10s_pct || 0;

        bnPrice.textContent = `$${p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        if (lastPrice > 0) {
            if (p > lastPrice) {
                bnPrice.style.color = "var(--accent-green)";
            } else if (p < lastPrice) {
                bnPrice.style.color = "var(--accent-pink)";
            }
            setTimeout(() => { bnPrice.style.color = "#ffffff"; }, 300);
        }
        lastPrice = p;

        bnVelocity.textContent = `${v >= 0 ? '+' : ''}${v.toFixed(2)} $/s`;
        bnVelocity.style.color = v >= 0 ? "var(--accent-green)" : "var(--accent-pink)";

        bnReturn.textContent = `${r >= 0 ? '+' : ''}${r.toFixed(3)}%`;
        bnReturn.className = `stat-val ${r >= 0 ? 'text-green' : 'text-pink'} font-mono`;

        // Update Sparkline
        if (data.binance.history && data.binance.history.length > 0) {
            priceHistory = data.binance.history;
            drawSparkline(priceHistory);
        }
    }

    // 2. Bayesian Fair Probabilities
    if (data.bayesian) {
        const qUp = (data.bayesian.q_up * 100).toFixed(1);
        const qDn = (data.bayesian.q_down * 100).toFixed(1);
        probUpLabel.textContent = `P(UP): ${qUp}%`;
        probDnLabel.textContent = `P(DOWN): ${qDn}%`;
        probBarUp.style.width = `${qUp}%`;
        probBarDn.style.width = `${qDn}%`;
        momScore.textContent = (data.bayesian.momentum || 0).toFixed(2);
    }

    // 3. Polymarket CLOB Book Spread
    if (data.polymarket) {
        const upB = data.polymarket.up_bid || 0;
        const upA = data.polymarket.up_ask || 0;
        const dnB = data.polymarket.down_bid || 0;
        const dnA = data.polymarket.down_ask || 0;
        clobSpread.textContent = `UP $${upB.toFixed(2)}/$${upA.toFixed(2)} • DN $${dnB.toFixed(2)}/$${dnA.toFixed(2)}`;
    }

    // 4. Quoting Engine & Complete Sets
    if (data.quotes) {
        const qUp = data.quotes.quote_up || 0;
        const qDn = data.quotes.quote_down || 0;
        const cSum = data.quotes.combined_cost || 0;
        const edge = data.quotes.edge_pct || 0;
        const limit = data.quotes.max_cost_limit || 0.96;

        quoteUp.textContent = `$${qUp.toFixed(2)}`;
        quoteDn.textContent = `$${qDn.toFixed(2)}`;
        quoteSum.textContent = `$${cSum.toFixed(3)}`;
        maxCostCap.textContent = `$${limit.toFixed(3)}`;
        edgeBadge.textContent = `+${edge.toFixed(1)}% ARB`;
    }

    // 5. Inventory & Exposure
    if (data.inventory) {
        const upSh = data.inventory.up_shares || 0;
        const upC = data.inventory.up_avg_cost || 0;
        const dnSh = data.inventory.down_shares || 0;
        const dnC = data.inventory.down_avg_cost || 0;
        const imb = data.inventory.net_imbalance || 0;
        const sets = data.inventory.complete_sets_merged || 0;

        invUpShares.textContent = upSh.toFixed(1);
        invUpCost.textContent = `avg $${upC.toFixed(3)}`;
        invDnShares.textContent = dnSh.toFixed(1);
        invDnCost.textContent = `avg $${dnC.toFixed(3)}`;
        imbalanceBadge.textContent = `DELTA: ${imb >= 0 ? '+' : ''}${imb.toFixed(1)}`;
        setsMerged.textContent = sets.toLocaleString();

        const pnl = data.inventory.realized_arb_pnl || 0;
        realizedPnl.textContent = `${pnl >= 0 ? '+' : '-'}$${Math.abs(pnl).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        const isPaused = data.inventory.is_stop_loss_triggered;
        if (isPaused) {
            pnlStatus.textContent = "ENGINE PAUSED";
            pnlStatus.className = "metric-badge font-mono text-yellow";
            btnResume.classList.remove("active");
            btnPause.classList.add("active");
        } else {
            pnlStatus.textContent = "ACTIVE QUOTING";
            pnlStatus.className = "metric-badge font-mono badge-active";
            btnResume.classList.add("active");
            btnPause.classList.remove("active");
        }

        // Capital Usage
        const invested = (upSh * upC) + (dnSh * dnC);
        const pct = ((invested / 300.0) * 100.0).toFixed(1);
        capitalUsage.textContent = `$${invested.toFixed(2)} / $300.00 (${pct}% in risk)`;
    }

    // 6. Ping / Latency Metrics to Binance and Polymarket
    if (data.latency) {
        const bnLat = data.latency.binance_ms || 22;
        const polyLat = data.latency.polymarket_ms || 38;
        if (binancePingVal) {
            binancePingVal.textContent = `${bnLat} ms`;
            binancePingVal.className = `ping-val font-bold ${bnLat < 60 ? 'text-green' : bnLat < 150 ? 'text-yellow' : 'text-red'}`;
        }
        if (polyPingVal) {
            polyPingVal.textContent = `${polyLat} ms`;
            polyPingVal.className = `ping-val font-bold ${polyLat < 80 ? 'text-green' : polyLat < 200 ? 'text-purple' : 'text-red'}`;
        }
    }

    // 7. Polymarket Official SDK Telemetry (Geoblock, Rate Limits, USDC Balance, Mode)
    if (data.polymarket && data.polymarket.sdk_telemetry) {
        const sdk = data.polymarket.sdk_telemetry;

        // Geoblock Pill & Card Badge
        const geo = sdk.geoblock || {};
        const isBlocked = geo.blocked === true;
        const geoVal = document.getElementById("polyGeoVal");
        const geoPill = document.getElementById("polyGeoPill");
        const geoBadge = document.getElementById("polyGeoBadge");

        if (geoVal) {
            geoVal.textContent = isBlocked ? `BLOCKED (${geo.country || 'US'})` : `ELIGIBLE (${geo.country || 'OK'})`;
            geoVal.className = isBlocked ? "geo-val text-pink font-bold" : "geo-val text-green font-bold";
        }
        if (geoPill) {
            if (isBlocked) geoPill.classList.add("blocked");
            else geoPill.classList.remove("blocked");
        }
        if (geoBadge) {
            geoBadge.textContent = isBlocked ? `RESTRICTED (${geo.country})` : `ELIGIBLE (${geo.country})`;
            geoBadge.className = isBlocked ? "metric-badge font-mono text-pink" : "metric-badge font-mono badge-active";
        }

        // Rate Limit Tokens
        const rl = sdk.rate_limits || {};
        const rateVal = document.getElementById("polyRateVal");
        const cardRate = document.getElementById("polyCardRateTokens");
        if (rateVal) {
            rateVal.textContent = `${Math.round(rl.order_tokens_remaining || 60)}/${rl.order_burst_capacity || 60}`;
        }
        if (cardRate) {
            cardRate.textContent = `${Math.round(rl.order_tokens_remaining || 60)}/${rl.order_burst_capacity || 60} Tokens (${rl.tier || 'Standard'})`;
        }

        // Live Balance
        const bal = sdk.balance || {};
        const polyUsdcBalance = document.getElementById("polyUsdcBalance");
        if (polyUsdcBalance) {
            const uBal = bal.usdc_balance !== undefined ? bal.usdc_balance : 300.0;
            polyUsdcBalance.textContent = `$${uBal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        }

        // Wallet Address
        const cardWalletAddr = document.getElementById("polyCardWalletAddr");
        if (cardWalletAddr) {
            const wa = sdk.wallet_address || "Not Configured";
            cardWalletAddr.textContent = (wa && wa.length > 10) ? `${wa.slice(0, 6)}...${wa.slice(-4)}` : wa;
            cardWalletAddr.title = wa;
        }

        // Execution Mode Pill & Badge
        const isLive = data.live_trading_active === true;
        const polyModeDot = document.getElementById("polyModeDot");
        const polyModeText = document.getElementById("polyModeText");
        const polyCardModeBadge = document.getElementById("polyCardModeBadge");
        const btnOpenPoly = document.getElementById("btnOpenPolyConfig");

        if (polyModeDot && polyModeText) {
            if (isLive) {
                polyModeDot.className = "mode-indicator mode-live";
                polyModeText.textContent = "MODE: LIVE CLOB";
                if (btnOpenPoly) btnOpenPoly.classList.add("mode-live-active");
            } else {
                polyModeDot.className = "mode-indicator mode-paper";
                polyModeText.textContent = "MODE: PAPER (SAFE)";
                if (btnOpenPoly) btnOpenPoly.classList.remove("mode-live-active");
            }
        }
        if (polyCardModeBadge) {
            polyCardModeBadge.textContent = isLive ? "LIVE CLOB" : "PAPER";
            polyCardModeBadge.className = isLive ? "badge badge-yellow font-mono" : "badge badge-purple font-mono";
        }
    }

    // 8. Real Analytics Snapshot
    if (data.analytics) {
        updateAnalyticsTab(data.analytics);
    }
}

// ==================== Real Analytics Rendering ====================

function updateAnalyticsTab(analytics) {
    statTotalTrades.textContent = analytics.total_trades || 0;
    statTradeSides.textContent = `UP: ${analytics.up_trades_count || 0} | DOWN: ${analytics.down_trades_count || 0}`;
    statTotalVolume.textContent = `$${(analytics.total_volume_usd || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
    statTotalSets.textContent = (analytics.total_complete_sets_merged || 0).toFixed(1);
    statAvgCost.textContent = `$${(analytics.avg_combined_cost || 0).toFixed(3)}`;
    statProfitMargin.textContent = `${(analytics.profit_margin_pct || 0).toFixed(2)}%`;
    statTotalFees.textContent = `$${(analytics.total_fees_paid || 0).toFixed(2)}`;
}

// ==================== Live HTML5 Canvas Sparkline ====================

function drawSparkline(points) {
    if (!priceSparkline) return;
    const ctx = priceSparkline.getContext("2d");
    const width = priceSparkline.parentElement.clientWidth || 300;
    const height = 48;
    priceSparkline.width = width;
    priceSparkline.height = height;

    if (points.length < 2) return;

    const prices = points.map(p => p.price);
    const minP = Math.min(...prices);
    const maxP = Math.max(...prices);
    const range = (maxP - minP) || 1;

    ctx.clearRect(0, 0, width, height);

    // Gradient Area Fill
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, "rgba(6, 182, 212, 0.35)");
    gradient.addColorStop(1, "rgba(6, 182, 212, 0.0)");

    // Draw Fill Path
    ctx.beginPath();
    points.forEach((pt, i) => {
        const x = (i / (points.length - 1)) * width;
        const y = height - ((pt.price - minP) / range) * (height - 12) - 6;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // Draw Line Path
    ctx.beginPath();
    points.forEach((pt, i) => {
        const x = (i / (points.length - 1)) * width;
        const y = height - ((pt.price - minP) / range) * (height - 12) - 6;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#06b6d4";
    ctx.lineWidth = 2;
    ctx.stroke();

    // Glowing latest-point circle
    const lastX = width;
    const lastY = height - ((prices[prices.length - 1] - minP) / range) * (height - 12) - 6;
    ctx.beginPath();
    ctx.arc(lastX - 2, lastY, 4, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.shadowBlur = 8;
    ctx.shadowColor = "#06b6d4";
}

// ==================== Risk Controls & Overrides ====================

btnResume.addEventListener("click", async () => {
    try {
        const res = await fetch("/api/control/resume", {
            method: "POST",
            headers: { "X-Auth-Token": authToken }
        });
        if (res.ok) {
            btnResume.classList.add("active");
            btnPause.classList.remove("active");
            showToast("Quoting engine resumed.", "success");
        }
    } catch (e) {
        showToast("Failed to resume quoting.", "error");
    }
});

btnPause.addEventListener("click", async () => {
    try {
        const res = await fetch("/api/control/pause", {
            method: "POST",
            headers: { "X-Auth-Token": authToken }
        });
        if (res.ok) {
            btnPause.classList.add("active");
            btnResume.classList.remove("active");
            showToast("Quoting paused safely.", "warning");
        }
    } catch (e) {
        showToast("Failed to pause quoting.", "error");
    }
});

btnEmergency.addEventListener("click", async () => {
    const accepted = await customConfirm({
        title: "EMERGENCY HALT TRIGGER",
        message: "Are you certain you want to activate the emergency circuit breaker? This will instantly cancel all resting bids and freeze quoting.",
        type: "emergency",
        confirmText: "HALT ENGINE NOW",
        cancelText: "Cancel",
        icon: "shield-alert"
    });

    if (accepted) {
        try {
            const res = await fetch("/api/control/emergency", {
                method: "POST",
                headers: { "X-Auth-Token": authToken }
            });
            if (res.ok) {
                showToast("EMERGENCY SHUTDOWN EXECUTED.", "error");
            }
        } catch (e) {
            showToast("Emergency command failed.", "error");
        }
    }
});

riskForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
        order_size_shares: parseFloat(inputOrderSize.value),
        max_inventory_imbalance: parseFloat(inputMaxImbalance.value),
        max_combined_cost: parseFloat(inputMaxCost.value)
    };

    try {
        const res = await fetch("/api/control/update_risk", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-Token": authToken
            },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("Risk limits updated & saved to Diskcache.", "success");
        } else {
            showToast(data.error || "Failed to update risk parameters.", "error");
        }
    } catch (err) {
        showToast("Network error updating risk parameters.", "error");
    }
});

// ==================== Sidebar & Navigation Handlers ====================

const sideNavDashboard = document.getElementById("sideNavDashboard");
const sideNavTrades = document.getElementById("sideNavTrades");
const sideNavSets = document.getElementById("sideNavSets");
const sideNavAnalytics = document.getElementById("sideNavAnalytics");
const btnSidebarToggle = document.getElementById("btnSidebarToggle");
const btnSidebarClose = document.getElementById("btnSidebarClose");
const sidebarBackdrop = document.getElementById("sidebarBackdrop");
const sidebar = document.getElementById("sidebar");

function openSidebar() {
    if (sidebar) sidebar.classList.add("open");
    if (sidebarBackdrop) sidebarBackdrop.classList.remove("hidden");
    refreshIcons();
}

function closeSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (sidebarBackdrop) sidebarBackdrop.classList.add("hidden");
}

if (btnSidebarToggle) {
    btnSidebarToggle.addEventListener("click", () => {
        if (sidebar && sidebar.classList.contains("open")) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });
}

if (btnSidebarClose) {
    btnSidebarClose.addEventListener("click", closeSidebar);
}

if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener("click", closeSidebar);
}

if (sideNavDashboard) {
    sideNavDashboard.addEventListener("click", () => {
        setSideNavActive("dashboard");
        window.scrollTo({ top: 0, behavior: "smooth" });
        if (window.innerWidth <= 1024) closeSidebar();
    });
}

if (sideNavTrades) {
    sideNavTrades.addEventListener("click", () => {
        setSideNavActive("trades");
        switchTab("trades");
        document.getElementById("sectionTrades").scrollIntoView({ behavior: "smooth" });
        if (window.innerWidth <= 1024) closeSidebar();
    });
}

if (sideNavSets) {
    sideNavSets.addEventListener("click", () => {
        setSideNavActive("sets");
        switchTab("sets");
        document.getElementById("sectionSets").scrollIntoView({ behavior: "smooth" });
        if (window.innerWidth <= 1024) closeSidebar();
    });
}

if (sideNavAnalytics) {
    sideNavAnalytics.addEventListener("click", () => {
        setSideNavActive("analytics");
        switchTab("analytics");
        document.getElementById("sectionAnalytics").scrollIntoView({ behavior: "smooth" });
        if (window.innerWidth <= 1024) closeSidebar();
    });
}

const sideNavMCP = document.getElementById("sideNavMCP");
if (sideNavMCP) {
    sideNavMCP.addEventListener("click", () => {
        setSideNavActive("mcp");
        switchTab("mcp");
        document.getElementById("sectionMCP").scrollIntoView({ behavior: "smooth" });
        if (window.innerWidth <= 1024) closeSidebar();
    });
}

function setSideNavActive(tab) {
    if (sideNavDashboard) sideNavDashboard.classList.toggle("active", tab === "dashboard");
    if (sideNavTrades) sideNavTrades.classList.toggle("active", tab === "trades");
    if (sideNavSets) sideNavSets.classList.toggle("active", tab === "sets");
    if (sideNavAnalytics) sideNavAnalytics.classList.toggle("active", tab === "analytics");
    if (sideNavMCP) sideNavMCP.classList.toggle("active", tab === "mcp");
}

// Window resize handler for canvas sparkline
window.addEventListener("resize", () => {
    if (priceHistory.length > 0) {
        drawSparkline(priceHistory);
    }
});

// ==================== Historical Logs & Tabs ====================

tabBtnTrades.addEventListener("click", () => {
    switchTab("trades");
    setSideNavActive("trades");
});
tabBtnSets.addEventListener("click", () => {
    switchTab("sets");
    setSideNavActive("sets");
});
tabBtnAnalytics.addEventListener("click", () => {
    switchTab("analytics");
    setSideNavActive("analytics");
});
if (tabBtnMCP) {
    tabBtnMCP.addEventListener("click", () => {
        switchTab("mcp");
        setSideNavActive("mcp");
    });
}

function switchTab(tab) {
    if (tabBtnTrades) tabBtnTrades.classList.toggle("active", tab === "trades");
    if (tabBtnSets) tabBtnSets.classList.toggle("active", tab === "sets");
    if (tabBtnAnalytics) tabBtnAnalytics.classList.toggle("active", tab === "analytics");
    if (tabBtnMCP) tabBtnMCP.classList.toggle("active", tab === "mcp");

    if (sectionTrades) sectionTrades.classList.toggle("hidden", tab !== "trades");
    if (sectionSets) sectionSets.classList.toggle("hidden", tab !== "sets");
    if (sectionAnalytics) sectionAnalytics.classList.toggle("hidden", tab !== "analytics");
    if (sectionMCP) sectionMCP.classList.toggle("hidden", tab !== "mcp");

    if (tab === "trades") loadTrades();
    if (tab === "sets") loadCompleteSets();
    if (tab === "analytics") loadAnalytics();
    if (tab === "mcp") loadMCPData();
    refreshIcons();
}

async function loadHistoricalLogs() {
    loadTrades();
    loadCompleteSets();
    loadAnalytics();
    loadMCPData();
}

async function loadTrades() {
    try {
        const res = await fetch("/api/trades?limit=50", { headers: { "X-Auth-Token": authToken } });
        if (res.ok) {
            const data = await res.json();
            const trades = data.trades || [];
            document.getElementById("tradeCountLabel").textContent = `${trades.length} Fills`;
            if (trades.length === 0) {
                tradeBody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No trades recorded yet.</td></tr>`;
                return;
            }
            tradeBody.innerHTML = trades.map(t => `
                <tr>
                    <td class="text-muted">${t.time_iso || ''}</td>
                    <td><span class="side-badge ${t.side.toLowerCase()}">${t.side}</span></td>
                    <td class="font-bold text-white">$${t.price.toFixed(3)}</td>
                    <td>${t.shares.toFixed(1)}</td>
                    <td>$${t.cost_usd.toFixed(2)}</td>
                    <td class="text-muted">$${t.fee_usd.toFixed(3)}</td>
                    <td><span class="badge font-mono">${t.execution_type}</span></td>
                </tr>
            `).join("");
        }
    } catch (e) {}
}

async function loadCompleteSets() {
    try {
        const res = await fetch("/api/complete_sets?limit=50", { headers: { "X-Auth-Token": authToken } });
        if (res.ok) {
            const data = await res.json();
            const sets = data.complete_sets || [];
            document.getElementById("setsCountLabel").textContent = `${sets.length} Merges`;
            if (sets.length === 0) {
                setsBody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No complete sets merged yet.</td></tr>`;
                return;
            }
            setsBody.innerHTML = sets.map(s => `
                <tr>
                    <td class="text-muted">${s.time_iso || ''}</td>
                    <td class="text-cyan font-bold">${s.sets_merged.toFixed(1)} sets</td>
                    <td>$${s.up_avg_cost.toFixed(3)}</td>
                    <td>$${s.down_avg_cost.toFixed(3)}</td>
                    <td class="font-bold text-yellow">$${s.combined_cost.toFixed(3)}</td>
                    <td class="text-green font-bold">+$${s.profit_locked.toFixed(2)}</td>
                    <td class="text-green font-bold">+$${s.cumulative_pnl.toFixed(2)}</td>
                </tr>
            `).join("");
        }
    } catch (e) {}
}

async function loadAnalytics() {
    try {
        const res = await fetch("/api/analytics", { headers: { "X-Auth-Token": authToken } });
        if (res.ok) {
            const data = await res.json();
            updateAnalyticsTab(data);
        }
    } catch (e) {}
}

// ==================== MCP Agent Manager Handlers ====================

let currentMcpLogs = [];

async function loadMCPData() {
    await Promise.all([
        loadMcpStats(),
        loadMcpKeys(),
        loadMcpLogs()
    ]);
}

async function loadMcpStats() {
    try {
        const res = await fetch("/api/mcp/stats", { headers: { "X-Auth-Token": authToken } });
        if (res.ok) {
            const data = await res.json();
            if (mcpStatActiveKeys) mcpStatActiveKeys.textContent = data.active_keys_count || 0;
            if (mcpStatTotalCalls) mcpStatTotalCalls.textContent = data.total_calls || 0;
            if (mcpStatAvgLatency) mcpStatAvgLatency.textContent = `${data.avg_latency_ms || 0.0} ms`;
            if (mcpStatSuccessRate) mcpStatSuccessRate.textContent = `${data.success_rate_pct || 100.0}%`;
            if (mcpStatErrors) mcpStatErrors.textContent = `${data.error_calls || 0} Invocations Failed`;
        }
    } catch (e) {}
}

async function loadMcpKeys() {
    try {
        const res = await fetch("/api/mcp/keys", { headers: { "X-Auth-Token": authToken } });
        if (res.ok) {
            const data = await res.json();
            renderMcpKeys(data.keys || []);
        }
    } catch (e) {}
}

function renderMcpKeys(keys) {
    if (!mcpKeysBody) return;
    if (keys.length === 0) {
        mcpKeysBody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No MCP API keys found. Create one to get started.</td></tr>`;
        return;
    }

    mcpKeysBody.innerHTML = keys.map(k => {
        const isEnabled = k.enabled === 1;
        const roleBadge = k.role === "read_write" ? `<span class="badge badge-purple">READ & WRITE</span>` : `<span class="badge badge-cyan">READ ONLY</span>`;
        const statusBadge = isEnabled ? `<span class="badge badge-active font-mono">ACTIVE</span>` : `<span class="badge font-mono text-red">DISABLED</span>`;
        return `
            <tr>
                <td class="font-bold text-white">${k.name}</td>
                <td><code class="font-mono text-cyan">${k.key_prefix}</code></td>
                <td>${roleBadge}</td>
                <td>${statusBadge}</td>
                <td class="font-bold">${k.usage_count} calls</td>
                <td class="text-muted">${k.last_used_at_iso || 'Never'}</td>
                <td>
                    <div class="profile-actions">
                        <button class="btn-table-action" onclick="toggleMcpKey(${k.id}, ${!isEnabled})" title="${isEnabled ? 'Disable Key' : 'Enable Key'}">
                            <i data-lucide="${isEnabled ? 'pause' : 'play'}" class="icon-xs"></i>
                            <span>${isEnabled ? 'Pause' : 'Resume'}</span>
                        </button>
                        <button class="btn-table-action btn-del" onclick="deleteMcpKey(${k.id}, '${k.name}')" title="Revoke and Delete Key">
                            <i data-lucide="trash-2" class="icon-xs"></i>
                            <span>Revoke</span>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join("");
    refreshIcons();
}

async function toggleMcpKey(keyId, enable) {
    try {
        const res = await fetch(`/api/mcp/keys/${keyId}/toggle`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Auth-Token": authToken
            },
            body: JSON.stringify({ enabled: enable })
        });
        if (res.ok) {
            showToast(`MCP API Key ${enable ? 'enabled' : 'disabled'}.`, "success");
            loadMCPData();
        }
    } catch (e) {
        showToast("Failed to toggle MCP API key status.", "error");
    }
}

async function deleteMcpKey(keyId, keyName) {
    const accepted = await customConfirm({
        title: "Revoke MCP API Key",
        message: `Are you sure you want to permanently revoke key "${keyName}"? Any AI agent using this key will immediately lose access.`,
        type: "danger",
        confirmText: "Revoke Key",
        cancelText: "Keep Active",
        icon: "shield-alert"
    });

    if (accepted) {
        try {
            const res = await fetch(`/api/mcp/keys/${keyId}`, {
                method: "DELETE",
                headers: { "X-Auth-Token": authToken }
            });
            if (res.ok) {
                showToast("MCP API Key permanently revoked.", "warning");
                loadMCPData();
            }
        } catch (e) {
            showToast("Failed to delete key.", "error");
        }
    }
}

async function loadMcpLogs() {
    try {
        const tool = mcpToolFilter ? mcpToolFilter.value : "";
        const url = tool ? `/api/mcp/logs?limit=50&tool=${encodeURIComponent(tool)}` : "/api/mcp/logs?limit=50";
        const res = await fetch(url, { headers: { "X-Auth-Token": authToken } });
        if (res.ok) {
            const data = await res.json();
            currentMcpLogs = data.logs || [];
            renderMcpLogs(currentMcpLogs);
        }
    } catch (e) {}
}

function renderMcpLogs(logs) {
    if (!mcpLogsBody) return;
    if (logs.length === 0) {
        mcpLogsBody.innerHTML = `<tr><td colspan="7" class="text-center text-muted">No MCP invocations logged for this filter.</td></tr>`;
        return;
    }

    mcpLogsBody.innerHTML = logs.map((l, index) => {
        const isSuccess = l.status === "SUCCESS";
        const statusBadge = isSuccess ? `<span class="badge badge-active font-mono">SUCCESS</span>` : `<span class="badge font-mono text-red">ERROR</span>`;
        const latClass = l.execution_time_ms < 20 ? 'text-green' : l.execution_time_ms < 100 ? 'text-yellow' : 'text-red';
        const clientIp = l.client_ip || '127.0.0.1';
        return `
            <tr>
                <td class="text-muted">${l.created_at_iso}</td>
                <td class="text-cyan font-bold">${l.key_name || 'Agent'}</td>
                <td><code class="font-mono text-yellow" style="font-size: 0.78rem;">${clientIp}</code></td>
                <td><span class="badge badge-purple font-mono">${l.tool_name}</span></td>
                <td>${statusBadge}</td>
                <td class="font-bold ${latClass}">${l.execution_time_ms.toFixed(1)} ms</td>
                <td>
                    <button class="btn-table-action btn-inspect" onclick="inspectMcpPayload(${index})">
                        <i data-lucide="eye" class="icon-xs"></i>
                        <span>Inspect</span>
                    </button>
                </td>
            </tr>
        `;
    }).join("");
    refreshIcons();
}

function inspectMcpPayload(index) {
    const record = currentMcpLogs[index];
    if (!record) return;

    const clientIp = record.client_ip || '127.0.0.1';
    mcpPayloadModalTitle.textContent = `TOOL: ${record.tool_name}`;
    mcpPayloadModalSubtitle.textContent = `Client: ${record.key_name || 'Agent'} • IP: ${clientIp} • Status: ${record.status} • Latency: ${record.execution_time_ms.toFixed(1)} ms • ${record.created_at_iso}`;

    mcpReqArgsJson.textContent = typeof record.request_json === 'object' ? JSON.stringify(record.request_json, null, 2) : record.request_args;
    mcpResDataJson.textContent = typeof record.response_json === 'object' ? JSON.stringify(record.response_json, null, 2) : record.response_data;

    mcpPayloadModal.classList.remove("hidden");
    refreshIcons();
}

// Attach handlers for key generation modal
if (btnOpenCreateKeyModal) {
    btnOpenCreateKeyModal.addEventListener("click", () => {
        mcpKeyName.value = "";
        mcpKeyRole.value = "read_write";
        generatedKeyBox.classList.add("hidden");
        mcpKeyErrorMsg.classList.add("hidden");
        mcpKeyActions.classList.remove("hidden");
        mcpCreateKeyModal.classList.remove("hidden");
        refreshIcons();
    });
}

function hideMcpKeyModal() {
    if (mcpCreateKeyModal) mcpCreateKeyModal.classList.add("hidden");
}

if (btnCloseMcpKeyModal) btnCloseMcpKeyModal.addEventListener("click", hideMcpKeyModal);
if (btnCancelMcpKey) btnCancelMcpKey.addEventListener("click", hideMcpKeyModal);

if (mcpKeyForm) {
    mcpKeyForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        mcpKeyErrorMsg.classList.add("hidden");
        const name = mcpKeyName.value.trim();
        const role = mcpKeyRole.value;

        try {
            const res = await fetch("/api/mcp/keys", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Auth-Token": authToken
                },
                body: JSON.stringify({ name, role })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                generatedKeyRaw.textContent = data.raw_key;
                generatedKeyBox.classList.remove("hidden");
                mcpKeyActions.classList.add("hidden");
                showToast("MCP API Key created! Copy your secret key.", "success");
                loadMCPData();
            } else {
                mcpKeyErrorMsg.textContent = data.error || "Failed to create MCP key.";
                mcpKeyErrorMsg.classList.remove("hidden");
            }
        } catch (err) {
            mcpKeyErrorMsg.textContent = "Network error creating MCP API Key.";
            mcpKeyErrorMsg.classList.remove("hidden");
        }
    });
}

if (btnCopyGeneratedKey) {
    btnCopyGeneratedKey.addEventListener("click", () => {
        navigator.clipboard.writeText(generatedKeyRaw.textContent);
        showToast("Secret MCP Key copied to clipboard!", "success");
    });
}

if (btnRefreshMCP) {
    btnRefreshMCP.addEventListener("click", () => {
        loadMCPData();
        showToast("MCP data refreshed.", "info");
    });
}

if (mcpToolFilter) {
    mcpToolFilter.addEventListener("change", () => {
        loadMcpLogs();
    });
}

// Payload modal closing
if (btnClosePayloadModal) btnClosePayloadModal.addEventListener("click", () => mcpPayloadModal.classList.add("hidden"));
if (btnClosePayloadModalBtn) btnClosePayloadModalBtn.addEventListener("click", () => mcpPayloadModal.classList.add("hidden"));
if (btnCopyReqArgs) {
    btnCopyReqArgs.addEventListener("click", () => {
        navigator.clipboard.writeText(mcpReqArgsJson.textContent);
        showToast("Request arguments copied to clipboard!", "success");
    });
}
if (btnCopyResData) {
    btnCopyResData.addEventListener("click", () => {
        navigator.clipboard.writeText(mcpResDataJson.textContent);
        showToast("Response payload copied to clipboard!", "success");
    });
}

// ==================== Polymarket Live SDK & Controls Modal ====================

const polyConfigModal = document.getElementById("polyConfigModal");
const btnOpenPolyConfig = document.getElementById("btnOpenPolyConfig");
const sideNavPolyConfig = document.getElementById("sideNavPolyConfig");
const btnClosePolyModal = document.getElementById("btnClosePolyModal");
const btnCancelPolyModal = document.getElementById("btnCancelPolyModal");
const polyConfigForm = document.getElementById("polyConfigForm");
const inputPolyPrivateKey = document.getElementById("inputPolyPrivateKey");
const inputPolyWalletAddress = document.getElementById("inputPolyWalletAddress");
const inputPolyProxyUrl = document.getElementById("inputPolyProxyUrl");
const inputPolyApiKey = document.getElementById("inputPolyApiKey");
const btnTogglePolyPk = document.getElementById("btnTogglePolyPk");
const btnRunPolyAudit = document.getElementById("btnRunPolyAudit");
const polyAuditOutput = document.getElementById("polyAuditOutput");
const radioModePaper = document.getElementById("radioModePaper");
const radioModeLive = document.getElementById("radioModeLive");
const polyModeActiveBadge = document.getElementById("polyModeActiveBadge");

async function openPolyConfigModal() {
    if (!polyConfigModal) return;
    polyConfigModal.classList.remove("hidden");
    refreshIcons();

    // Load stored settings from server
    try {
        const res = await fetch("/api/polymarket/status", {
            headers: { "X-Auth-Token": authToken }
        });
        if (res.ok) {
            const data = await res.json();
            const cfg = data.config || {};
            if (inputPolyWalletAddress) inputPolyWalletAddress.value = cfg.wallet_address || "";
            if (inputPolyProxyUrl) inputPolyProxyUrl.value = cfg.proxy_url || "";
            if (inputPolyApiKey) inputPolyApiKey.value = cfg.api_key || "";
            if (inputPolyPrivateKey) {
                inputPolyPrivateKey.value = "";
                inputPolyPrivateKey.placeholder = cfg.private_key_masked ? `Current: ${cfg.private_key_masked}` : "0x... or leave blank for paper mode";
            }

            if (data.live_trading_active) {
                if (radioModeLive) radioModeLive.checked = true;
                if (polyModeActiveBadge) {
                    polyModeActiveBadge.textContent = "LIVE CLOB ACTIVE";
                    polyModeActiveBadge.className = "badge badge-yellow font-mono";
                }
            } else {
                if (radioModePaper) radioModePaper.checked = true;
                if (polyModeActiveBadge) {
                    polyModeActiveBadge.textContent = "PAPER SIMULATION";
                    polyModeActiveBadge.className = "badge badge-green font-mono";
                }
            }
        }
    } catch (e) {
        console.error("Failed to load Polymarket config:", e);
    }
}

function hidePolyConfigModal() {
    if (polyConfigModal) polyConfigModal.classList.add("hidden");
}

if (btnOpenPolyConfig) btnOpenPolyConfig.addEventListener("click", openPolyConfigModal);
if (sideNavPolyConfig) sideNavPolyConfig.addEventListener("click", openPolyConfigModal);
if (btnClosePolyModal) btnClosePolyModal.addEventListener("click", hidePolyConfigModal);
if (btnCancelPolyModal) btnCancelPolyModal.addEventListener("click", hidePolyConfigModal);

if (btnTogglePolyPk && inputPolyPrivateKey) {
    btnTogglePolyPk.addEventListener("click", () => {
        const isPw = inputPolyPrivateKey.type === "password";
        inputPolyPrivateKey.type = isPw ? "text" : "password";
        btnTogglePolyPk.innerHTML = `<i data-lucide="${isPw ? 'eye-off' : 'eye'}" class="icon-xs"></i>`;
        refreshIcons();
    });
}

if (btnRunPolyAudit) {
    btnRunPolyAudit.addEventListener("click", async () => {
        if (!polyAuditOutput) return;
        polyAuditOutput.textContent = "Running diagnostics against https://polymarket.com/api/geoblock and CLOB balance...";

        try {
            const proxyVal = inputPolyProxyUrl ? inputPolyProxyUrl.value.trim() : "";
            const res = await fetch("/api/polymarket/test_connection", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Auth-Token": authToken
                },
                body: JSON.stringify({ proxy_url: proxyVal || undefined })
            });
            const data = await res.json();

            if (res.ok && data.status === "SUCCESS") {
                const geo = data.geoblock || {};
                const bal = data.balance || {};
                const geoText = geo.blocked ? `RESTRICTED (Country: ${geo.country}, IP: ${geo.ip})` : `ELIGIBLE (Country: ${geo.country}, IP: ${geo.ip})`;

                polyAuditOutput.textContent = [
                    `[GEOBLOCK AUDIT]: ${geoText}`,
                    `[COLLATERAL BALANCE]: $${(bal.usdc_balance || 300).toFixed(2)} USDC.e`,
                    `[ALLOWANCE]: $${(bal.allowance || 10000).toFixed(2)}`,
                    `[AUTH STATUS]: ${data.is_authenticated ? 'AUTHENTICATED (Secure Client Ready)' : 'SIMULATION / PUBLIC ONLY'}`,
                    geo.blocked ? "\n⚠️ WARNING: Your IP is in a geoblocked region. Please configure an outbound Proxy URL (e.g. EU region) to place real orders." : "\n✅ IP is compliant and eligible for order execution."
                ].join("\n");

                showToast("Connection audit completed.", geo.blocked ? "warning" : "success");
            } else {
                polyAuditOutput.textContent = `Diagnostic Error: ${data.error || 'Failed to query Polymarket servers'}`;
                showToast("Audit failed.", "error");
            }
        } catch (err) {
            polyAuditOutput.textContent = `Network Exception: ${err.message}`;
            showToast("Network error running audit.", "error");
        }
    });
}

if (polyConfigForm) {
    polyConfigForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        const isLiveChosen = radioModeLive && radioModeLive.checked;
        const pk = inputPolyPrivateKey ? inputPolyPrivateKey.value.trim() : "";
        const wa = inputPolyWalletAddress ? inputPolyWalletAddress.value.trim() : "";
        const prx = inputPolyProxyUrl ? inputPolyProxyUrl.value.trim() : "";
        const ak = inputPolyApiKey ? inputPolyApiKey.value.trim() : "";

        if (isLiveChosen) {
            const confirmed = await showCustomConfirm(
                "Enable Live CLOB Trading?",
                "WARNING: You are switching to LIVE CLOB EXECUTION. Real limit orders will be placed on Polymarket using real USDC collateral within strict $300 bankroll limits. Proceed?",
                "Enable Live Trading",
                "Keep Paper Mode"
            );
            if (!confirmed) return;
        }

        try {
            const res = await fetch("/api/polymarket/config", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Auth-Token": authToken
                },
                body: JSON.stringify({
                    private_key: pk || undefined,
                    wallet_address: wa || undefined,
                    proxy_url: prx,
                    api_key: ak || undefined,
                    live_trading_enabled: isLiveChosen
                })
            });

            const data = await res.json();
            if (res.ok && data.status === "SUCCESS") {
                hidePolyConfigModal();
                showToast(
                    isLiveChosen ? "LIVE TRADING ENABLED! Real CLOB orders active." : "Paper Trading Mode active (Safe).",
                    isLiveChosen ? "warning" : "success"
                );
            } else {
                showToast(data.error || "Failed to update Polymarket settings.", "error");
            }
        } catch (err) {
            showToast("Network error saving configuration.", "error");
        }
    });
}

// Expose functions globally for onclick attributes in dynamically generated rows
window.toggleMcpKey = toggleMcpKey;
window.deleteMcpKey = deleteMcpKey;
window.inspectMcpPayload = inspectMcpPayload;

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
    refreshIcons();
    checkExistingAuth();
});
