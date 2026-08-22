// ==================================================================
// BINANCE URGENT PAIR VOLUME MONITORING - JAVASCRIPT ENGINE
// ==================================================================

document.addEventListener('DOMContentLoaded', () => {
    initUrgentApp();
});

let urgentPairsData = [];

function initUrgentApp() {
    loadUrgentPairs();
    loadUrgentLogs();

    // Auto-refresh pair volumes every 10 seconds
    setInterval(() => {
        loadUrgentPairs(true);
    }, 10000);

    // Auto-refresh dispatch history logs every 30 seconds
    setInterval(() => {
        loadUrgentLogs();
    }, 30000);

    // Add Pair Form Event Listener
    const addPairForm = document.getElementById('add-pair-form');
    if (addPairForm) {
        addPairForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const symbolInput = document.getElementById('new-pair-symbol');
            const minimumInput = document.getElementById('new-pair-minimum');

            let symbol = symbolInput.value.trim().toUpperCase();
            const base_minimum = parseFloat(minimumInput.value);

            if (!symbol || isNaN(base_minimum) || base_minimum <= 0) {
                showToast('⚠️ Вкажіть коректний символ монети та позитивний базовий мінімум!', 'amber');
                return;
            }

            if (!symbol.endsWith('USDT')) {
                symbol += 'USDT';
            }

            try {
                const resp = await fetch('/api/urgent/pairs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol, base_minimum, enabled: true })
                });
                const res = await resp.json();

                if (res.ok) {
                    showToast(`✅ Пару ${symbol} успішно додано до термінового моніторингу!`, 'emerald');
                    symbolInput.value = '';
                    minimumInput.value = '';
                    loadUrgentPairs();
                } else {
                    showToast(`❌ Помилка додавання: ${res.error}`, 'red');
                }
            } catch (err) {
                showToast(`❌ Помилка з'єднання: ${err.message}`, 'red');
            }
        });
    }
}

// Fetch Monitored Pairs from Backend
async function loadUrgentPairs(isSilent = false) {
    const stripsContainer = document.getElementById('urgent-strips-list');
    const timerLabel = document.getElementById('pairs-updated-timer');

    try {
        const resp = await fetch('/api/urgent/pairs');
        const data = await resp.json();

        if (data.ok && Array.isArray(data.pairs)) {
            urgentPairsData = data.pairs;
            renderUrgentStrips(urgentPairsData);
            if (timerLabel) {
                const now = new Date();
                timerLabel.innerText = `Оновлено: ${now.toLocaleTimeString()}`;
            }
        } else if (!isSilent && stripsContainer) {
            stripsContainer.innerHTML = `<div class="bg-darkCard border border-darkBorder rounded-xl p-8 text-center text-red-400 font-mono">Не вдалося завантажити списки термінового моніторингу.</div>`;
        }
    } catch (e) {
        console.error('Error loading urgent pairs:', e);
        if (!isSilent && stripsContainer) {
            stripsContainer.innerHTML = `<div class="bg-darkCard border border-darkBorder rounded-xl p-8 text-center text-red-400 font-mono">Помилка мережі при завантаженні даних: ${e.message}</div>`;
        }
    }
}

// Render Monitored Pairs Strips ("Смужки")
function renderUrgentStrips(pairs) {
    const stripsContainer = document.getElementById('urgent-strips-list');
    if (!stripsContainer) return;

    if (!pairs || pairs.length === 0) {
        stripsContainer.innerHTML = `
            <div class="bg-darkCard border border-darkBorder rounded-xl p-8 text-center text-slate-400 font-mono flex flex-col items-center gap-2">
                <span class="text-3xl">📭</span>
                <span>Немає доданих пар для термінового моніторингу.</span>
                <span class="text-xs text-slate-500">Використайте форму вище щоб додати пару (напр. ACE, BTC, ETH...).</span>
            </div>
        `;
        return;
    }

    let html = '';

    pairs.forEach(pair => {
        const isExceeded = pair.vol_1m_coins >= pair.base_minimum && pair.enabled;
        const progressPct = Math.min(100, pair.progress_pct || 0);

        const changeClass = pair.price_change_24h >= 0 ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40' : 'bg-red-500/20 text-red-400 border-red-500/40';
        const changeSign = pair.price_change_24h >= 0 ? '+' : '';

        const progressColor = isExceeded
            ? 'bg-gradient-to-r from-yellow-500 via-amber-400 to-red-500 animate-pulse'
            : progressPct >= 80
            ? 'bg-gradient-to-r from-blue-500 to-amber-400'
            : 'bg-gradient-to-r from-binanceBlue to-emerald-400';

        const stripBorderClass = isExceeded
            ? 'border-red-500/80 shadow-[0_0_20px_rgba(246,70,93,0.3)] bg-red-950/10'
            : pair.enabled
            ? 'border-darkBorder hover:border-slate-600 bg-darkCard'
            : 'border-darkBorder/40 bg-darkCard/50 opacity-75';

        const formattedLastPrice = pair.last_alert_price ? `$${formatNumber(pair.last_alert_price, 4)} USDT` : '—';
        const formattedLastTime = pair.last_alert_time && pair.last_alert_time !== 'Не надсилалося'
            ? `<span class="text-emerald-400 font-bold">${pair.last_alert_time}</span>`
            : `<span class="text-slate-500 font-normal">Не надсилалося</span>`;

        let logoText = (pair.base_currency || '').toString();
        if (logoText.toUpperCase().includes('PEPE') || (pair.symbol && pair.symbol.toUpperCase().includes('PEPE'))) {
            logoText = 'PEPE';
        } else if (logoText.startsWith('1000')) {
            logoText = logoText.replace(/^1000/, '');
        }

        html += `
            <div class="${stripBorderClass} border rounded-xl p-4 shadow-xl flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-4 transition-all duration-300 font-mono">
                
                <!-- 1. НАЗВА ПАРИ & ЦІНА -->
                <div class="flex items-center gap-3.5 min-w-[200px]">
                    <div class="w-10 h-10 rounded-lg bg-darkHeader border border-darkBorder flex items-center justify-center font-black text-binanceYellow ${logoText.length > 5 ? 'text-[10px]' : logoText.length > 3 ? 'text-xs' : 'text-sm'} shadow">
                        ${logoText}
                    </div>
                    <div class="flex flex-col gap-0.5">
                        <div class="flex items-center gap-2">
                            <span class="font-extrabold text-base text-white tracking-wide">${pair.symbol}</span>
                            <span class="border text-[10px] px-1.5 py-0.2 rounded font-bold ${changeClass}">
                                ${changeSign}${pair.price_change_24h.toFixed(2)}%
                            </span>
                        </div>
                        <div class="text-xs text-slate-400">
                            Ціна: <span class="text-white font-bold">$${formatNumber(pair.price, 4)}</span>
                        </div>
                    </div>
                </div>

                <!-- 2. АКТИВНЕ ВІКНО ДЛЯ ВВОДУ ДАНИХ (БАЗОВИЙ МІНІМУМ ЗА 1 ХВ) -->
                <div class="flex flex-col gap-1 min-w-[240px] bg-darkBg border border-darkBorder p-2.5 rounded-lg">
                    <div class="flex justify-between items-center text-[10px] text-slate-400 font-bold">
                        <span>БАЗОВИЙ МІНІМУМ (1 ХВ)</span>
                        <span class="text-binanceYellow">${pair.base_currency}</span>
                    </div>
                    <div class="flex items-center gap-1.5">
                        <input type="number" step="any" id="threshold-input-${pair.symbol}" value="${pair.base_minimum}"
                            class="w-full bg-darkCard border border-darkBorder rounded px-2.5 py-1.5 text-xs text-white focus:outline-none focus:border-binanceYellow font-mono font-bold">
                        <button onclick="savePairThreshold('${pair.symbol}')"
                            class="bg-binanceYellow hover:bg-yellow-400 text-black font-bold px-2.5 py-1.5 rounded transition text-xs flex items-center gap-1 whitespace-nowrap shadow">
                            💾 Зберегти
                        </button>
                    </div>
                </div>

                <!-- 3. ПОТОЧНИЙ 1-ХВИЛИННИЙ ОБСЯГ & ПРОГРЕС -->
                <div class="flex flex-col gap-1 min-w-[220px] flex-1">
                    <div class="flex justify-between items-center text-xs">
                        <span class="text-slate-400 text-[11px]">ФАКТИЧНИЙ 1-ХВ ОБСЯГ:</span>
                        <span class="font-bold ${isExceeded ? 'text-red-400 animate-pulse' : 'text-emerald-400'}">
                            ${formatNumber(pair.vol_1m_coins, 0)} ${pair.base_currency}
                        </span>
                    </div>
                    <div class="w-full h-2.5 bg-darkBg rounded-full border border-darkBorder overflow-hidden relative">
                        <div class="h-full ${progressColor} transition-all duration-500 rounded-full" style="width: ${progressPct}%"></div>
                    </div>
                    <div class="flex justify-between items-center text-[10px] text-slate-400">
                        <span>Прогрес: <b class="${progressPct >= 100 ? 'text-red-400' : 'text-slate-200'}">${pair.progress_pct.toFixed(1)}%</b></span>
                        <span>USDT: <b class="text-slate-200">$${formatMoney(pair.vol_1m_usdt)}</b></span>
                    </div>
                </div>

                <!-- 4. ЧАС СПРАЦЮВАННЯ І НАПРАВЛЕННЯ ПОВІДОМЛЕННЯ -->
                <div class="flex flex-col gap-0.5 min-w-[160px] bg-darkBg/60 border border-darkBorder/80 p-2 rounded-lg">
                    <span class="text-[10px] text-slate-400 font-semibold">ЧАС СПРАЦЮВАННЯ</span>
                    <div class="text-xs font-bold text-slate-200">
                        ${formattedLastTime}
                    </div>
                </div>

                <!-- 5. ВАРТІСТЬ МОНЕТИ ПІД ЧАС ВІДПРАВЛЕННЯ ПОВІДОМЛЕННЯ -->
                <div class="flex flex-col gap-0.5 min-w-[150px] bg-darkBg/60 border border-darkBorder/80 p-2 rounded-lg">
                    <span class="text-[10px] text-slate-400 font-semibold">ЦІНА ПРИ ВІДПРАВЦІ</span>
                    <div class="text-xs font-bold text-binanceYellow">
                        ${formattedLastPrice}
                    </div>
                </div>

                <!-- 6. СТАТУС & ДІЇ (ОПЦІЇ) -->
                <div class="flex items-center gap-2">
                    <label class="relative inline-flex items-center cursor-pointer" title="Увімкнути/Вимкнути моніторинг">
                        <input type="checkbox" id="toggle-${pair.symbol}" ${pair.enabled ? 'checked' : ''}
                            onchange="togglePairMonitoring('${pair.symbol}', this.checked)" class="sr-only peer">
                        <div class="w-9 h-5 bg-slate-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-500"></div>
                    </label>

                    <button onclick="triggerTestAlert('${pair.symbol}')" title="Надіслати тестове повідомлення"
                        class="bg-darkHeader hover:bg-slate-700 border border-darkBorder text-slate-200 font-bold p-2 rounded text-xs transition flex items-center gap-1">
                        🧪
                    </button>

                    <button onclick="deletePair('${pair.symbol}')" title="Видалити пару з моніторингу"
                        class="bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-400 font-bold p-2 rounded text-xs transition">
                        🗑️
                    </button>
                </div>

            </div>
        `;
    });

    stripsContainer.innerHTML = html;
}

// Save Pair Threshold
async function savePairThreshold(symbol) {
    const input = document.getElementById(`threshold-input-${symbol}`);
    if (!input) return;

    const base_minimum = parseFloat(input.value);
    if (isNaN(base_minimum) || base_minimum <= 0) {
        showToast(`⚠️ Вкажіть більший за 0 базовий мінімум для ${symbol}!`, 'amber');
        return;
    }

    try {
        const resp = await fetch('/api/urgent/pairs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, base_minimum, enabled: true })
        });
        const res = await resp.json();

        if (res.ok) {
            showToast(`✅ Базовий мінімум для ${symbol} збережено: ${formatNumber(base_minimum, 0)}`, 'emerald');
            loadUrgentPairs();
        } else {
            showToast(`❌ Помилка збереження: ${res.error}`, 'red');
        }
    } catch (e) {
        showToast(`❌ Помилка мережі: ${e.message}`, 'red');
    }
}

// Toggle Pair Monitoring Enabled/Disabled
async function togglePairMonitoring(symbol, enabled) {
    const pair = urgentPairsData.find(p => p.symbol === symbol);
    const base_minimum = pair ? pair.base_minimum : 300000000.0;

    try {
        const resp = await fetch('/api/urgent/pairs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, base_minimum, enabled })
        });
        const res = await resp.json();
        if (res.ok) {
            showToast(`Статус моніторингу для ${symbol} оновлено: ${enabled ? 'УВІМКНЕНО' : 'ВИМКНЕНО'}`, enabled ? 'emerald' : 'amber');
            loadUrgentPairs();
        }
    } catch (e) {
        showToast(`❌ Помилка оновлення статусу: ${e.message}`, 'red');
    }
}

// Delete Monitored Pair
async function deletePair(symbol) {
    if (!confirm(`Ви дійсно бажаєте видалити пару ${symbol} з термінового моніторингу?`)) return;

    try {
        const resp = await fetch(`/api/urgent/pairs/${symbol}`, {
            method: 'DELETE'
        });
        const res = await resp.json();
        if (res.ok) {
            showToast(`Пару ${symbol} видалено.`, 'amber');
            loadUrgentPairs();
        } else {
            showToast(`❌ Помилка видалення: ${res.error}`, 'red');
        }
    } catch (e) {
        showToast(`❌ Помилка з'єднання: ${e.message}`, 'red');
    }
}

// Trigger Test Alert for Pair
async function triggerTestAlert(symbol) {
    showToast(`⏳ Відправка тестового повідомлення для ${symbol}...`, 'blue');

    try {
        const resp = await fetch('/api/urgent/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol })
        });
        const res = await resp.json();
        if (res.ok) {
            showToast(`🎉 Тестове сповіщення для ${symbol} успішно надіслано у Telegram!`, 'emerald');
        } else {
            showToast(`❌ Помилка Telegram: ${res.error}`, 'red');
        }
    } catch (e) {
        showToast(`❌ Помилка з'єднання: ${e.message}`, 'red');
    }
}

// Fetch and Render Dispatch Logs History
async function loadUrgentLogs() {
    const tbody = document.getElementById('urgent-logs-body');
    if (!tbody) return;

    try {
        const resp = await fetch('/api/urgent/logs?limit=50');
        const data = await resp.json();

        if (data.ok && Array.isArray(data.logs)) {
            if (data.logs.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500 font-mono">Історія відправки термінових сповіщень порожня.</td></tr>`;
                return;
            }

            let html = '';
            data.logs.forEach(log => {
                const baseCurrency = log.symbol.replace('USDT', '');
                html += `
                    <tr class="hover:bg-slate-800/40 transition">
                        <td class="py-2 px-3 text-slate-300">${log.sent_at}</td>
                        <td class="py-2 px-3 font-bold text-white">${log.symbol}</td>
                        <td class="py-2 px-3 text-slate-400 font-mono">${formatNumber(log.base_minimum, 0)} ${baseCurrency}</td>
                        <td class="py-2 px-3 text-emerald-400 font-mono font-bold">${formatNumber(log.volume_coins, 0)} ${baseCurrency}</td>
                        <td class="py-2 px-3 text-red-400 font-bold">+${log.surge_pct.toFixed(1)}%</td>
                        <td class="py-2 px-3 text-right text-binanceYellow font-bold">$${log.price.toFixed(4)}</td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        }
    } catch (e) {
        console.error('Error loading urgent logs:', e);
    }
}

// Utility Toast Notifications
function showToast(message, type = 'blue') {
    const toast = document.getElementById('urgent-status-toast');
    if (!toast) return;

    toast.classList.remove('hidden', 'bg-emerald-500/20', 'text-emerald-400', 'border-emerald-500/40', 'bg-red-500/20', 'text-red-400', 'border-red-500/40', 'bg-amber-500/20', 'text-amber-400', 'border-amber-500/40', 'bg-blue-500/20', 'text-blue-400', 'border-blue-500/40');

    if (type === 'emerald') {
        toast.className = 'p-3 rounded-lg text-xs font-mono border bg-emerald-500/20 text-emerald-400 border-emerald-500/40 shadow-lg';
    } else if (type === 'red') {
        toast.className = 'p-3 rounded-lg text-xs font-mono border bg-red-500/20 text-red-400 border-red-500/40 shadow-lg';
    } else if (type === 'amber') {
        toast.className = 'p-3 rounded-lg text-xs font-mono border bg-amber-500/20 text-amber-400 border-amber-500/40 shadow-lg';
    } else {
        toast.className = 'p-3 rounded-lg text-xs font-mono border bg-blue-500/20 text-blue-400 border-blue-500/40 shadow-lg';
    }

    toast.innerHTML = message;

    setTimeout(() => {
        toast.classList.add('hidden');
    }, 6000);
}

// Format Numbers
function formatNumber(val, decimals = 0) {
    if (val === undefined || val === null || isNaN(val)) return '0';
    return Number(val).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatMoney(val) {
    if (val === undefined || val === null || isNaN(val)) return '0.00';
    return Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
