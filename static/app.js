// --- BINANCE ANOMALY RADAR FRONTEND APPLICATION ---

let ws = null;
let leaderboardData = [];
let currentFilter = 'all';
let searchKeyword = '';
let inspectedSymbol = 'BTCUSDT';
let inspectedPrice = 0.0;
let directBinanceWs = null;

// Sorting state
let sortColumn = 'score';
let sortDirection = 'desc';

// DOM Elements - Screener
const leaderboardBody = document.getElementById('leaderboard-body');
const searchInput = document.getElementById('search-input');
const pairsCountEl = document.getElementById('pairs-count');
const dbRecordsCountEl = document.getElementById('db-records-count');

const inspectorTitle = document.getElementById('inspector-title');
const inspectorPriceBadge = document.getElementById('inspector-price-badge');
const inspectorUnder2Badge = document.getElementById('inspector-under2-badge');
const bidsList = document.getElementById('bids-list');
const asksList = document.getElementById('asks-list');
const tradeTapeBody = document.getElementById('trade-tape-body');
const sqliteLogsBody = document.getElementById('sqlite-logs-body');

// Initialize WebApp
document.addEventListener('DOMContentLoaded', () => {
    initLocalWebSocket();
    setupFilterEvents();
    inspectSymbol('BTCUSDT');
});

// Setup local WebSocket connection
function initLocalWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = (window.location.port === '' || window.location.port === '80' || window.location.port === '443')
        ? `${protocol}//${host}/ws`
        : `${protocol}//${window.location.hostname}:8765/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('Connected to Local WebSocket Server.');
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'snapshot') {
                leaderboardData = data.data || [];
                if (pairsCountEl) pairsCountEl.innerText = data.total_pairs || leaderboardData.length;
                if (dbRecordsCountEl) dbRecordsCountEl.innerText = data.db_records || 0;
                renderLeaderboard();
            } else if (data.type === 'anomaly') {
                handleNewAnomalyAlert(data.data);
            } else if (data.type === 'emergency_alert') {
                handleEmergencyAlertPush(data.data);
            }
        } catch (e) {
            console.error('WS Message Parse Error:', e);
        }
    };

    ws.onclose = () => {
        console.warn('Local WS disconnected. Reconnecting in 3s...');
        setTimeout(initLocalWebSocket, 3000);
    };

    ws.onerror = (err) => {
        console.error('Local WS Error:', err);
    };
}

// Setup Event Listeners for Screener
function setupFilterEvents() {
    if (document.getElementById('btn-filter-all')) {
        document.getElementById('btn-filter-all').addEventListener('click', (e) => {
            setActiveFilterButton(e.target);
            currentFilter = 'all';
            renderLeaderboard();
        });
    }

    if (document.getElementById('btn-filter-under2')) {
        document.getElementById('btn-filter-under2').addEventListener('click', (e) => {
            setActiveFilterButton(e.target);
            currentFilter = 'under2';
            renderLeaderboard();
        });
    }

    if (document.getElementById('btn-filter-topscore')) {
        document.getElementById('btn-filter-topscore').addEventListener('click', (e) => {
            setActiveFilterButton(e.target);
            currentFilter = 'topscore';
            renderLeaderboard();
        });
    }

    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            searchKeyword = e.target.value.trim().toUpperCase();
            renderLeaderboard();
        });
    }
}

function setActiveFilterButton(activeBtn) {
    ['btn-filter-all', 'btn-filter-under2', 'btn-filter-topscore'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.className = 'bg-darkCard border border-darkBorder text-slate-400 hover:bg-slate-800 text-xs px-3 py-1.5 rounded transition';
    });
    if (activeBtn) activeBtn.className = 'bg-binanceYellow text-black font-semibold text-xs px-3 py-1.5 rounded transition';
}

// Handle Column Sort
function handleSort(column) {
    if (sortColumn === column) {
        sortDirection = (sortDirection === 'desc') ? 'asc' : 'desc';
    } else {
        sortColumn = column;
        sortDirection = (column === 'symbol') ? 'asc' : 'desc';
    }
    updateSortIcons();
    renderLeaderboard();
}

function updateSortIcons() {
    const columns = ['symbol', 'price', 'volume_surge_pct', 'volatility_pct', 'trades_count', 'orderbook_density', 'score'];
    columns.forEach(col => {
        const iconEl = document.getElementById(`sort-icon-${col}`);
        if (iconEl) {
            if (col === sortColumn) {
                iconEl.innerText = sortDirection === 'desc' ? '▼' : '▲';
                iconEl.className = 'text-[10px] text-binanceYellow font-bold ml-1';
            } else {
                iconEl.innerText = '';
                iconEl.className = 'text-[10px] text-slate-500 ml-1';
            }
        }
    });
}

// Filter and Render Leaderboard Table
function renderLeaderboard() {
    if (!leaderboardBody) return;

    let filtered = leaderboardData.filter(item => {
        if (searchKeyword && !item.symbol.includes(searchKeyword)) return false;
        if (currentFilter === 'under2' && !item.is_under_2usd) return false;
        if (currentFilter === 'topscore' && item.score < 50) return false;
        return true;
    });

    // Dynamic Column Sorting
    filtered.sort((a, b) => {
        let valA = a[sortColumn];
        let valB = b[sortColumn];

        if (valA === undefined || valA === null) valA = 0;
        if (valB === undefined || valB === null) valB = 0;

        if (typeof valA === 'string') {
            return sortDirection === 'asc'
                ? valA.localeCompare(valB)
                : valB.localeCompare(valA);
        } else {
            return sortDirection === 'asc'
                ? valA - valB
                : valB - valA;
        }
    });

    if (filtered.length === 0) {
        leaderboardBody.innerHTML = `
            <tr>
                <td colspan="9" class="py-8 text-center text-slate-500 font-mono">
                    No active coins match filter criteria.
                </td>
            </tr>
        `;
        return;
    }

    let html = '';
    filtered.slice(0, 50).forEach((item, index) => {
        const isSelected = item.symbol === inspectedSymbol;
        const priceFormatted = item.price < 1.0 ? `$${item.price.toFixed(6)}` : `$${item.price.toFixed(4)}`;
        const surgePct = item.volume_surge_pct;
        const volaPct = item.volatility_pct;
        const surgeWidth = Math.min(100, Math.max(5, (surgePct / 500) * 100));

        const scoreColorClass = item.score >= 80 ? 'text-binanceYellow font-bold' : item.score >= 50 ? 'text-emerald-400 font-bold' : 'text-slate-300';
        
        const priceBadgeHtml = item.is_under_2usd 
            ? `<span class="bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 text-[10px] px-1.5 py-0.5 rounded font-mono font-bold ml-1.5">&lt;$2</span>`
            : '';

        html += `
            <tr class="hover:bg-slate-800/50 transition duration-150 cursor-pointer ${isSelected ? 'bg-slate-800/80 border-l-4 border-binanceYellow' : ''}"
                onclick="inspectSymbol('${item.symbol}')">
                <td class="py-3 px-4 text-center font-bold ${index < 3 ? 'text-binanceYellow' : 'text-slate-500'}">${index + 1}</td>
                <td class="py-3 px-4 font-bold text-white flex items-center">
                    ${item.symbol}
                    ${priceBadgeHtml}
                </td>
                <td class="py-3 px-4 text-slate-200 font-mono">${priceFormatted}</td>
                <td class="py-3 px-4">
                    <div class="flex items-center gap-2">
                        <span class="font-bold ${surgePct >= 150 ? 'text-binanceYellow' : 'text-slate-300'}">+${surgePct.toFixed(1)}%</span>
                        <div class="flex-1 bg-darkHeader h-2 rounded-full overflow-hidden border border-slate-700 max-w-[90px]">
                            <div class="h-full bg-gradient-to-r from-emerald-500 to-binanceYellow" style="width: ${surgeWidth}%"></div>
                        </div>
                    </div>
                </td>
                <td class="py-3 px-4 text-slate-300 font-mono">+${volaPct.toFixed(1)}%</td>
                <td class="py-3 px-4 text-slate-300 font-mono">${item.trades_count.toLocaleString()}</td>
                <td class="py-3 px-4 text-slate-300 font-mono">$${(item.orderbook_density / 1000).toFixed(1)}K</td>
                <td class="py-3 px-4 text-sm font-mono ${scoreColorClass}">${item.score.toFixed(1)}</td>
                <td class="py-3 px-4 text-center">
                    <button onclick="event.stopPropagation(); inspectSymbol('${item.symbol}')"
                        class="bg-darkHeader hover:bg-binanceYellow hover:text-black border border-darkBorder text-slate-300 text-[11px] px-2.5 py-1 rounded transition font-sans font-semibold">
                        INSPECT
                    </button>
                </td>
            </tr>
        `;
    });

    leaderboardBody.innerHTML = html;
}

// Handle real-time anomaly push
function handleNewAnomalyAlert(anomaly) {
    if (dbRecordsCountEl) {
        let count = parseInt(dbRecordsCountEl.innerText || '0') + 1;
        dbRecordsCountEl.innerText = count;
    }
}

function handleEmergencyAlertPush(alertData) {
    if (currentView === 'ace') {
        loadAceStats();
    }
}

// Inspect Selected Symbol
function inspectSymbol(symbol) {
    inspectedSymbol = symbol;
    if (inspectorTitle) inspectorTitle.innerHTML = `ASSET INSPECTOR: <span class="text-binanceYellow">${symbol}</span>`;
    
    const found = leaderboardData.find(c => c.symbol === symbol);
    if (found) {
        inspectedPrice = found.price;
        if (inspectorPriceBadge) inspectorPriceBadge.innerText = inspectedPrice < 1.0 ? `$${inspectedPrice.toFixed(6)}` : `$${inspectedPrice.toFixed(4)}`;
        if (inspectorUnder2Badge) {
            inspectorUnder2Badge.style.display = found.is_under_2usd ? 'inline-block' : 'none';
        }
    }

    renderLeaderboard();
    connectDirectBinanceWs(symbol);
    fetchSqliteAnomalyLogs(symbol);
}

// Direct Binance WS Connection for Inspector
function connectDirectBinanceWs(symbol) {
    if (directBinanceWs) {
        directBinanceWs.close();
    }

    const streamSymbol = symbol.toLowerCase();
    const url = `wss://stream.binance.com:9443/ws/${streamSymbol}@trade/${streamSymbol}@depth10@100ms`;

    directBinanceWs = new WebSocket(url);

    if (tradeTapeBody) tradeTapeBody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-slate-500">Streaming trades...</td></tr>';

    directBinanceWs.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.e === 'trade') {
                renderTradeTapeRow(msg);
            } else if (msg.bids && msg.asks) {
                renderOrderBookDepth(msg);
            }
        } catch (e) {
            console.error('Binance Direct WS Parse Error:', e);
        }
    };
}

// Render Trade Tape
function renderTradeTapeRow(trade) {
    if (!tradeTapeBody) return;
    const price = float(trade.p);
    const qty = float(trade.q);
    const isSell = trade.m;
    const timeStr = new Date(trade.T).toLocaleTimeString();

    const priceFormatted = price < 1.0 ? price.toFixed(6) : price.toFixed(4);
    const qtyFormatted = qty >= 1000 ? `${(qty / 1000).toFixed(1)}K` : qty.toFixed(1);

    const row = document.createElement('tr');
    row.className = isSell ? 'text-binanceSell' : 'text-binanceBuy';
    row.innerHTML = `
        <td class="py-1 text-slate-400">${timeStr}</td>
        <td class="py-1 font-bold">${priceFormatted}</td>
        <td class="py-1 font-mono">${qtyFormatted}</td>
        <td class="py-1 text-right font-bold">${isSell ? 'SELL' : 'BUY'}</td>
    `;

    if (tradeTapeBody.children.length > 20) {
        tradeTapeBody.removeChild(tradeTapeBody.lastChild);
    }
    tradeTapeBody.insertBefore(row, tradeTapeBody.firstChild);
}

// Render Order Book Depth (±1%)
function renderOrderBookDepth(depth) {
    if (!bidsList || !asksList) return;
    let bidsHtml = '';
    let asksHtml = '';

    const topBids = depth.bids.slice(0, 5);
    const topAsks = depth.asks.slice(0, 5);

    topBids.forEach(bid => {
        const price = float(bid[0]);
        const qty = float(bid[1]);
        bidsHtml += `
            <div class="flex justify-between items-center px-2 py-1 rounded depth-bar-bid text-binanceBuy text-[11px]">
                <span>${price < 1 ? price.toFixed(6) : price.toFixed(4)}</span>
                <span class="text-slate-300 font-mono">${(qty * price / 1000).toFixed(1)}K</span>
            </div>
        `;
    });

    topAsks.forEach(ask => {
        const price = float(ask[0]);
        const qty = float(ask[1]);
        asksHtml += `
            <div class="flex justify-between items-center px-2 py-1 rounded depth-bar-ask text-binanceSell text-[11px]">
                <span>${price < 1 ? price.toFixed(6) : price.toFixed(4)}</span>
                <span class="text-slate-300 font-mono">${(qty * price / 1000).toFixed(1)}K</span>
            </div>
        `;
    });

    bidsList.innerHTML = bidsHtml;
    asksList.innerHTML = asksHtml;
}

// Fetch Anomaly Logs from Backend SQLite API
async function fetchSqliteAnomalyLogs(symbol) {
    try {
        const resp = await fetch(`/api/anomalies?symbol=${symbol}&limit=10`);
        if (resp.ok && sqliteLogsBody) {
            const logs = await resp.json();
            if (logs.length === 0) {
                sqliteLogsBody.innerHTML = '<tr><td colspan="4" class="py-4 text-center text-slate-500">No anomaly history recorded yet in SQLite.</td></tr>';
                return;
            }

            let html = '';
            logs.forEach(log => {
                const dateStr = new Date(log.detected_at).toLocaleTimeString();
                html += `
                    <tr>
                        <td class="py-1 text-slate-400">${dateStr}</td>
                        <td class="py-1 text-binanceYellow font-bold">+${log.volume_surge_pct.toFixed(1)}%</td>
                        <td class="py-1 text-slate-300">+${log.volatility_pct.toFixed(1)}%</td>
                        <td class="py-1 text-right font-bold text-emerald-400">${log.calculated_score.toFixed(1)}</td>
                    </tr>
                `;
            });
            sqliteLogsBody.innerHTML = html;
        }
    } catch (e) {
        console.error('Fetch SQLite Anomaly Logs Error:', e);
    }
}


// Utility Formatters
function formatMoney(val) {
    if (val >= 1e6) return `${(val / 1e6).toFixed(2)}M`;
    if (val >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
    return val.toFixed(2);
}

function formatNum(val) {
    if (val >= 1e6) return `${(val / 1e6).toFixed(2)}M`;
    if (val >= 1e3) return `${(val / 1e3).toFixed(1)}K`;
    return val.toLocaleString();
}

function float(val) {
    return parseFloat(val || 0);
}
