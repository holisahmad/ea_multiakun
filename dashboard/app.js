/**
 * STMA Dashboard — app.js (Mobile First Architecture)
 * Ultra-responsive operator client for Smart Trade Multi Account Controller
 */

// ── Smart API Discovery (Automatic VPS / Server Origin Detection) ──
function getInitialApiUrl() {
  const saved = localStorage.getItem('STMA_API_URL');
  if (saved) return saved;
  if (window.STMA_API) return window.STMA_API;
  
  if (window.location && window.location.protocol.startsWith('http') && window.location.port !== '5500') {
    return window.location.origin;
  }
  return 'http://127.0.0.1:8000';
}

let API = getInitialApiUrl();
let AUTH_TOKEN = localStorage.getItem('STMA_AUTH_TOKEN') || 'KunciAmanSTMA2026';
const REFRESH_INTERVAL = 1500; // 1.5 seconds for snappy realtime streaming

function getHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Auth-Token': AUTH_TOKEN,
  };
}

let isConnected = false;
let refreshTimer = null;
let pendingAction = null;
let activeLogFilter = 'all';

// Pagination state
let currentTradePage = 1;
const TRADES_PAGE_SIZE = 6;
let cachedTradesHistory = [];

let currentSigPage = 1;
const SIGS_PAGE_SIZE = 6;
let cachedSignalsLog = [];

// ── Local Time (WIB) Clock ─────────────────────────────────
function updateClock() {
  const now = new Date();
  const timeEl = document.getElementById('headerTime');
  if (timeEl) {
    const hours = String(now.getHours()).padStart(2, '0');
    const mins = String(now.getMinutes()).padStart(2, '0');
    const secs = String(now.getSeconds()).padStart(2, '0');
    timeEl.textContent = `${hours}:${mins}:${secs} WIB`;
  }
}
setInterval(updateClock, 1000);
updateClock();

// ── Tab Navigation (Mobile & Desktop) ──────────────────────
function switchTab(tabId) {
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  const targetPane = document.getElementById(tabId);
  if (targetPane) targetPane.classList.add('active');

  document.querySelectorAll('.bottom-tab-bar .tab-btn').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
  });

  document.querySelectorAll('.desktop-tabs-nav .nav-tab').forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabId);
  });

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ── Trade Sub-Tab Navigation ───────────────────────────────
function switchTradeSubtab(subtab) {
  const btnPos = document.getElementById('btnSubtabPos');
  const btnHist = document.getElementById('btnSubtabHistory');
  const panelPos = document.getElementById('panelTradePositions');
  const panelHist = document.getElementById('panelTradeHistory');

  if (subtab === 'positions') {
    if (btnPos) btnPos.classList.add('active');
    if (btnHist) btnHist.classList.remove('active');
    if (panelPos) panelPos.style.display = 'block';
    if (panelHist) panelHist.style.display = 'none';
  } else {
    if (btnPos) btnPos.classList.remove('active');
    if (btnHist) btnHist.classList.add('active');
    if (panelPos) panelPos.style.display = 'none';
    if (panelHist) panelHist.style.display = 'block';
  }
}

// ── Manual Refresh Visual Trigger ─────────────────────────
async function manualRefresh() {
  const btn = document.getElementById('refreshBtn');
  if (btn) btn.classList.add('spinning');
  try {
    await refreshAll();
    toast('Data real-time diperbarui', 'info');
  } finally {
    setTimeout(() => {
      if (btn) btn.classList.remove('spinning');
    }, 500);
  }
}

// ── API Fetch Wrapper ──────────────────────────────────────
async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(API + path, {
      ...options,
      headers: {
        ...getHeaders(),
        ...(options.headers || {}),
      },
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    if (err.name === 'TypeError') {
      setConnectionState(false);
    }
    throw err;
  }
}

// ── Connection Indicator ───────────────────────────────────
function setConnectionState(connected) {
  isConnected = connected;
  const dot = document.getElementById('connDot');
  const label = document.getElementById('connLabel');
  if (connected) {
    if (dot) dot.className = 'conn-dot connected';
    if (label) label.textContent = 'Server Online';
  } else {
    if (dot) dot.className = 'conn-dot disconnected';
    if (label) label.textContent = 'Terputus';
  }
}

// ── Master Refresh ─────────────────────────────────────────
async function refreshAll() {
  try {
    await Promise.all([
      loadPerformance(),
      loadSystemState(),
      loadAccounts(),
      loadCycles(),
      loadPositions(),
      loadTradesHistory(),
      loadSignals(),
      loadCommands(),
      loadRiskEvents(),
      loadSystemEvents(),
    ]);
    setConnectionState(true);
  } catch (err) {
    setConnectionState(false);
    console.warn('Refresh error:', err.message);
  }
}

// ── Trading Performance Summary ────────────────────────────
async function loadPerformance() {
  const data = await apiFetch('/dashboard/performance');
  if (!data) return;

  // Overview metrics
  const totalTradesEl = document.getElementById('perfTotalTrades');
  if (totalTradesEl) totalTradesEl.textContent = `${data.total_trades_today} Trades`;

  const tradeBreakdownEl = document.getElementById('perfTradeBreakdown');
  if (tradeBreakdownEl) tradeBreakdownEl.textContent = `${data.buy_trades_today} BUY • ${data.sell_trades_today} SELL`;

  const floatPnlEl = document.getElementById('perfFloatingPnl');
  if (floatPnlEl) {
    const val = data.total_floating_pnl;
    floatPnlEl.textContent = (val >= 0 ? '+$' : '-$') + Math.abs(val).toFixed(2);
    floatPnlEl.className = 'box-value ' + (val > 0 ? 'positive' : val < 0 ? 'negative' : '');
  }

  const realPnlEl = document.getElementById('perfRealizedPnl');
  if (realPnlEl) {
    const val = data.total_realized_pnl;
    realPnlEl.textContent = (val >= 0 ? '+$' : '-$') + Math.abs(val).toFixed(2);
    realPnlEl.className = 'box-value ' + (val > 0 ? 'positive' : val < 0 ? 'negative' : '');
  }

  const volEl = document.getElementById('perfTotalVolume');
  if (volEl) volEl.textContent = `${data.total_volume_today.toFixed(2)} Lot`;

  const netPnlEl = document.getElementById('perfNetPnl');
  if (netPnlEl) {
    const val = data.net_pnl_today;
    netPnlEl.textContent = `Net: ${(val >= 0 ? '+$' : '-$') + Math.abs(val).toFixed(2)}`;
    netPnlEl.style.color = val > 0 ? 'var(--green)' : val < 0 ? 'var(--red)' : 'var(--text-muted)';
  }

  // Active cycles explainer banner
  const cycleTitle = document.getElementById('cycleStatusTitle');
  const cycleBadge = document.getElementById('cycleStatusBadge');
  const cycleDesc  = document.getElementById('cycleStatusDesc');
  const barCycles  = document.getElementById('barCycles');

  const activeCnt = data.active_cycles_count || 0;
  if (barCycles) barCycles.textContent = activeCnt;

  if (activeCnt > 0) {
    if (cycleTitle) cycleTitle.textContent = `Status Siklus: ${activeCnt} Siklus Aktif Berjalan`;
    if (cycleBadge) {
      cycleBadge.textContent = 'Sedang Trading';
      cycleBadge.className = 'status-pill status-pill-online';
    }
    if (cycleDesc) {
      cycleDesc.innerHTML = `<strong>⚡ Siklus sedang aktif mengelola posisi.</strong> Sinyal order baru untuk simbol XAUUSD ditahan sementara hingga siklus ini selesai agar portofolio tidak overtrade / overlayer.`;
    }
  } else {
    if (cycleTitle) cycleTitle.textContent = 'Status Siklus: Standby (0 Aktif)';
    if (cycleBadge) {
      cycleBadge.textContent = 'Siap Entry Baru';
      cycleBadge.className = 'status-pill status-pill-online';
    }
    if (cycleDesc) {
      cycleDesc.innerHTML = `<strong>ℹ️ Apa itu Siklus (Cycle)?</strong> 1 Siklus adalah 1 rangkaian transaksi bertingkat untuk 1 arah sinyal (Akun 1 Primary → Akun 2 Secondary → Akun 3 Tertiary). Saat ini tidak ada siklus aktif; sistem siap menerima order baru.`;
    }
  }
}

// ── System State ───────────────────────────────────────────
async function loadSystemState() {
  const data = await apiFetch('/dashboard/state');

  const barStrategy = document.getElementById('barStrategy');
  if (barStrategy) {
    barStrategy.textContent = data.strategy_enabled ? 'Active' : 'Paused';
    barStrategy.className = 'chip-val ' + (data.strategy_enabled ? 'status-active' : 'negative');
  }

  const stateEnv = document.getElementById('stateEnv');
  if (stateEnv) stateEnv.textContent = data.environment.toUpperCase();

  const stateLive = document.getElementById('stateLive');
  if (stateLive) {
    stateLive.textContent = data.live_trading_enabled ? 'LIVE (Real Mode)' : 'DEMO';
    stateLive.style.color = data.live_trading_enabled ? 'var(--cyan)' : 'var(--green)';
  }

  const stateProtection = document.getElementById('stateProtection');
  if (stateProtection) {
    stateProtection.textContent = data.protection_active ? '🛡 ACTIVE' : 'Normal';
    stateProtection.style.color = data.protection_active ? 'var(--red)' : '';
  }

  const stateCycles = document.getElementById('stateCycles');
  if (stateCycles) stateCycles.textContent = data.active_cycles;

  const envBadge = document.getElementById('envBadge');
  if (envBadge) {
    envBadge.textContent = data.environment.toUpperCase();
    envBadge.className = 'env-pill' + (data.environment === 'live' ? ' live' : '');
  }

  const modeTag = document.getElementById('summaryModeTag');
  if (modeTag) {
    modeTag.textContent = data.live_trading_enabled ? 'LIVE BROKER' : 'DEMO MODE';
  }
}

// ── Accounts ───────────────────────────────────────────────
async function loadAccounts() {
  const accounts = await apiFetch('/dashboard/accounts');
  const fullContainer = document.getElementById('accountsList');
  const overviewContainer = document.getElementById('overviewAccountsList');
  const navBadge = document.getElementById('navAccBadge');
  const barOnlineAccounts = document.getElementById('barOnlineAccounts');
  
  const onlineCount = accounts.filter(a => a.connected && !a.is_stale).length;
  if (navBadge) navBadge.textContent = `${onlineCount}/${accounts.length}`;
  if (barOnlineAccounts) barOnlineAccounts.textContent = `${onlineCount}/${accounts.length}`;

  if (!accounts.length) {
    const emptyHtml = '<div class="empty-state">Belum ada akun MT5 yang terdaftar</div>';
    if (fullContainer) fullContainer.innerHTML = emptyHtml;
    if (overviewContainer) overviewContainer.innerHTML = emptyHtml;
    return;
  }

  const cardsHtml = accounts.map(acc => {
    const pnl = acc.floating_pnl || 0;
    const pnlClass = pnl >= 0 ? 'positive' : 'negative';
    const pnlStr = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
    const balStr = acc.balance != null ? acc.balance.toFixed(2) : '0.00';
    const eqStr  = acc.equity != null ? acc.equity.toFixed(2) : '0.00';
    const mlStr  = acc.margin_level_pct != null ? acc.margin_level_pct.toFixed(0) + '%' : '—';

    const isLiveOnline = acc.connected && !a.is_stale;
    let dotColor = isLiveOnline ? 'var(--green)' : 'var(--red)';
    let statusText = isLiveOnline ? 'Live Connected' : 'MT5 Closed / Offline';
    let statusBadgeClass = isLiveOnline ? 'status-pill-online' : 'status-pill-offline';

    let ageStr = '';
    if (acc.data_age_seconds != null) {
      const s = Math.round(acc.data_age_seconds);
      if (s < 60) ageStr = `${s}d lalu`;
      else if (s < 3600) ageStr = `${Math.round(s/60)}m lalu`;
      else ageStr = `${Math.round(s/3600)}j lalu`;
    }

    const accName = acc.alias === 'account_1' ? 'Akun 1 (Primary)' : (acc.alias === 'account_2' ? 'Akun 2 (Secondary)' : 'Akun 3 (Tertiary)');
    const cacheNotice = isLiveOnline ? '' : '<span style="color:var(--text-muted);font-size:9px;margin-left:4px">(Snapshot Terakhir)</span>';

    return `
    <div class="account-card-modern ${isLiveOnline ? '' : 'account-card-offline'}">
      <div class="acc-card-top">
        <div class="acc-alias-group">
          <span class="acc-alias">${accName}</span>
          <span class="acc-role-pill role--${acc.role}">${acc.role.toUpperCase()}</span>
        </div>
        <div class="acc-status-indicator ${statusBadgeClass}">
          <span style="width:7px;height:7px;border-radius:50%;background:${dotColor}"></span>
          <span>${statusText}</span>
        </div>
      </div>
      <div class="acc-metrics-matrix">
        <div class="m-cell">
          <span class="m-cell-label">Balance ${cacheNotice}</span>
          <span class="m-cell-val">$${balStr}</span>
        </div>
        <div class="m-cell">
          <span class="m-cell-label">Equity</span>
          <span class="m-cell-val">$${eqStr}</span>
        </div>
        <div class="m-cell">
          <span class="m-cell-label">Float P/L</span>
          <span class="m-cell-val ${pnlClass}">${pnlStr}</span>
        </div>
        <div class="m-cell">
          <span class="m-cell-label">Margin %</span>
          <span class="m-cell-val">${mlStr}</span>
        </div>
        <div class="m-cell">
          <span class="m-cell-label">Posisi Aktif</span>
          <span class="m-cell-val">${acc.open_positions}</span>
        </div>
      </div>
      <div class="acc-footer">
        <span>Login ID: ${acc.login || '—'}</span>
        <span>${isLiveOnline ? 'Streaming Aktif' : 'Terakhir Aktif: ' + (ageStr || 'Offline')}</span>
      </div>
    </div>`;
  }).join('');

  if (fullContainer) fullContainer.innerHTML = cardsHtml;
  if (overviewContainer) overviewContainer.innerHTML = cardsHtml;
}

// ── Cycles ─────────────────────────────────────────────────
async function loadCycles() {
  const cycles = await apiFetch('/dashboard/cycles');
  const container = document.getElementById('cyclesList');
  const countBadge = document.getElementById('cycleCount');
  if (countBadge) countBadge.textContent = cycles.length;

  const dotBadge = document.getElementById('tradeDotBadge');
  if (dotBadge) dotBadge.style.display = cycles.length > 0 ? 'block' : 'none';

  if (!cycles.length) {
    if (container) container.innerHTML = '<div class="empty-state">Tidak ada siklus aktif saat ini (Standby)</div>';
    return;
  }

  if (container) {
    container.innerHTML = cycles.map(c => {
      const pnl = (c.floating_pnl_usd || 0) + (c.realized_pnl_usd || 0);
      const pnlClass = pnl >= 0 ? 'positive' : 'negative';
      const pnlStr = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
      const dirClass = c.direction === 'BUY' ? 'dir--buy' : 'dir--sell';

      const age = Math.round((Date.now() - new Date(c.started_at).getTime()) / 1000);
      const ageStr = age < 60 ? `${age}d` : age < 3600 ? `${Math.round(age/60)}m` : `${Math.round(age/3600)}j`;

      return `
      <div class="cycle-card-mobile">
        <div class="cycle-card-top">
          <div style="display:flex;align-items:center;gap:6px">
            <span class="cycle-id-badge">Siklus #${c.cycle_id.slice(0, 8)}</span>
            <span class="dir-badge ${dirClass}">${c.direction}</span>
          </div>
          <span class="state-pill">${c.state}</span>
        </div>
        <div class="cycle-card-body">
          <div class="cycle-details-left">
            <span><strong>${c.symbol}</strong></span>
            <span>${c.total_positions} Posisi Terbuka</span>
            <span>Durasi: ${ageStr}</span>
          </div>
          <div class="cycle-pnl-val ${pnlClass}">${pnlStr} USD</div>
        </div>
        <button class="cycle-btn-close" onclick="closeCycle('${c.cycle_id}')">
          ✕ Tutup Semua Posisi Siklus Ini
        </button>
      </div>`;
    }).join('');
  }
}

// ── Positions ──────────────────────────────────────────────
async function loadPositions() {
  const positions = await apiFetch('/dashboard/positions');
  const container = document.getElementById('positionsList');
  const countBadge = document.getElementById('posCount');
  const subCountBadge = document.getElementById('posSubCount');
  const barPos = document.getElementById('barPositions');
  const navBadge = document.getElementById('navTradeBadge');
  const btnCloseAll = document.getElementById('btnCloseAllPos');

  if (countBadge) countBadge.textContent = positions.length;
  if (subCountBadge) subCountBadge.textContent = positions.length;
  if (barPos) barPos.textContent = positions.length;
  if (navBadge) navBadge.textContent = positions.length;
  if (btnCloseAll) btnCloseAll.style.display = positions.length > 0 ? 'inline-block' : 'none';

  if (!positions.length) {
    if (container) container.innerHTML = '<div class="empty-state">Tidak ada posisi terbuka saat ini</div>';
    return;
  }

  if (container) {
    container.innerHTML = positions.map(p => {
      const pnl = p.floating_pnl || 0;
      const pnlClass = pnl >= 0 ? 'positive' : 'negative';
      const pnlStr = (pnl >= 0 ? '+' : '') + pnl.toFixed(2);
      const dirColor = p.direction === 'BUY' ? 'var(--green)' : 'var(--red)';

      const accName = p.account_alias === 'account_1' ? 'Akun 1 (Primary)' : (p.account_alias === 'account_2' ? 'Akun 2 (Secondary)' : 'Akun 3 (Tertiary)');
      const layerText = p.layer ? `Layer ${p.layer}` : 'Stage 1';

      return `
      <div class="pos-card-mobile">
        <div class="pos-card-head">
          <div style="display:flex;align-items:center;gap:6px">
            <span class="pos-acc">${accName}</span>
            <span class="acc-role-pill role--primary">${layerText}</span>
            <span style="font-weight:800;color:${dirColor}">${p.direction}</span>
            <span>${p.volume} lot</span>
          </div>
          <span class="pos-ticket">#${p.position_ticket}</span>
        </div>
        <div class="pos-metrics-row">
          <div>
            <div class="pos-cell-lbl">Open Price</div>
            <div class="pos-cell-data">${p.open_price?.toFixed(2) || '—'}</div>
          </div>
          <div>
            <div class="pos-cell-lbl">Take Profit</div>
            <div class="pos-cell-data">${p.tp?.toFixed(2) || '—'}</div>
          </div>
          <div>
            <div class="pos-cell-lbl">Stop Loss</div>
            <div class="pos-cell-data">${p.sl?.toFixed(2) || '—'}</div>
          </div>
          <div>
            <div class="pos-cell-lbl">Floating P/L</div>
            <div class="pos-cell-data ${pnlClass}">${pnlStr} USD</div>
          </div>
        </div>
        <button class="btn-pos-close" onclick="closePosition('${p.account_alias}', '${p.cycle_id || ''}')">
          ✕ Tutup Posisi Ini
        </button>
      </div>`;
    }).join('');
  }
}

// ── Trades History with Pagination ─────────────────────────
async function loadTradesHistory() {
  const history = await apiFetch('/dashboard/trades-history?limit=100');
  cachedTradesHistory = history || [];

  const histBadge = document.getElementById('tradeHistCount');
  const histTotalTag = document.getElementById('tradeHistTotalTag');
  if (histBadge) histBadge.textContent = cachedTradesHistory.length;
  if (histTotalTag) histTotalTag.textContent = `${cachedTradesHistory.length} Transaksi`;

  renderTradeHistoryPage();
}

function renderTradeHistoryPage() {
  const container = document.getElementById('tradeHistoryList');
  if (!container) return;

  if (!cachedTradesHistory.length) {
    container.innerHTML = '<div class="empty-state">Belum ada riwayat transaksi broker</div>';
    return;
  }

  const totalPages = Math.max(1, Math.ceil(cachedTradesHistory.length / TRADES_PAGE_SIZE));
  if (currentTradePage > totalPages) currentTradePage = totalPages;
  if (currentTradePage < 1) currentTradePage = 1;

  const startIdx = (currentTradePage - 1) * TRADES_PAGE_SIZE;
  const pageItems = cachedTradesHistory.slice(startIdx, startIdx + TRADES_PAGE_SIZE);

  container.innerHTML = pageItems.map(t => {
    const isBuy = t.direction === 'BUY';
    const dirClass = isBuy ? 'dir--buy' : 'dir--sell';
    const isManual = t.source_type === 'MANUAL';
    const sourcePill = isManual ? '<span class="source-pill-manual">👤 MANUAL</span>' : '<span class="source-pill-bot">🤖 BOT ALGO</span>';

    const timeStr = t.recorded_at ? new Date(t.recorded_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' WIB' : '—';

    return `
    <div class="trade-hist-card">
      <div class="trade-hist-top">
        <div style="display:flex;align-items:center;gap:6px">
          ${sourcePill}
          <span style="font-weight:700;font-size:13px">${t.account_name}</span>
          <span class="dir-badge ${dirClass}">${t.direction}</span>
          <span>${t.volume} lot</span>
        </div>
        <span class="status-pill-accepted">✓ ${t.retcode_desc}</span>
      </div>
      <div class="trade-hist-meta">
        <div><strong>Harga:</strong> ${t.executed_price?.toFixed(2) || '—'}</div>
        <div><strong>TP/SL:</strong> ${t.tp_points || '—'} / ${t.sl_points || '—'}</div>
        <div><strong>Tiket Deal:</strong> #${t.deal_ticket || t.order_ticket || '—'}</div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted)">
        <span>Eksekusi: ${timeStr}</span>
        <span>Layer ${t.layer || 1} • ${t.action}</span>
      </div>
    </div>`;
  }).join('');

  // Pagination buttons & indicator
  const indicator = document.getElementById('tradePageIndicator');
  if (indicator) indicator.textContent = `Halaman ${currentTradePage} dari ${totalPages}`;

  const btnPrev = document.getElementById('btnPrevTradePage');
  const btnNext = document.getElementById('btnNextTradePage');
  if (btnPrev) btnPrev.disabled = (currentTradePage <= 1);
  if (btnNext) btnNext.disabled = (currentTradePage >= totalPages);
}

function changeTradePage(delta) {
  currentTradePage += delta;
  renderTradeHistoryPage();
}

// ── Inbound Signals Log with Pagination ─────────────────────
async function loadSignals() {
  const signals = await apiFetch('/dashboard/signals?limit=100');
  cachedSignalsLog = signals || [];

  const sigCount = document.getElementById('sigCount');
  const sigBadge = document.getElementById('sigBadge');
  if (sigCount) sigCount.textContent = cachedSignalsLog.length;
  if (sigBadge) sigBadge.textContent = `${cachedSignalsLog.length} Sinyal`;

  renderSignalsPage();
}

function renderSignalsPage() {
  const container = document.getElementById('signalsList');
  if (!container) return;

  if (!cachedSignalsLog.length) {
    container.innerHTML = '<div class="empty-state">Belum ada sinyal masuk</div>';
    return;
  }

  const totalPages = Math.max(1, Math.ceil(cachedSignalsLog.length / SIGS_PAGE_SIZE));
  if (currentSigPage > totalPages) currentSigPage = totalPages;
  if (currentSigPage < 1) currentSigPage = 1;

  const startIdx = (currentSigPage - 1) * SIGS_PAGE_SIZE;
  const pageItems = cachedSignalsLog.slice(startIdx, startIdx + SIGS_PAGE_SIZE);

  container.innerHTML = pageItems.map(s => {
    const isManual = s.source_type === 'MANUAL';
    const sourcePill = isManual ? '<span class="source-pill-manual">👤 MANUAL</span>' : '<span class="source-pill-bot">🤖 BOT ALGO</span>';
    
    const isAccepted = s.status === 'ACCEPTED' || s.status === 'USED';
    const statusPill = isAccepted 
      ? '<span class="status-pill-accepted">✓ DITERIMA & DIEKSEKUSI</span>' 
      : '<span class="status-pill-rejected">✕ DITOLAK SERVER/RISK</span>';

    const dirClass = s.direction === 'BUY' ? 'dir--buy' : 'dir--sell';
    const timeStr = s.received_at ? new Date(s.received_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' WIB' : '—';

    const rejectHtml = s.reject_reason 
      ? `<div class="signal-reject-msg"><strong>Alasan Penolakan:</strong> ${s.reject_reason}</div>` 
      : '';

    return `
    <div class="signal-log-card">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div style="display:flex;align-items:center;gap:6px">
          ${sourcePill}
          <span class="dir-badge ${dirClass}">${s.direction}</span>
          <span style="font-weight:700">${s.symbol}</span>
        </div>
        ${statusPill}
      </div>
      ${rejectHtml}
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-top:2px">
        <span>Sumber: ${s.source}</span>
        <span>Waktu: ${timeStr}</span>
      </div>
    </div>`;
  }).join('');

  const indicator = document.getElementById('sigPageIndicator');
  if (indicator) indicator.textContent = `Halaman ${currentSigPage} dari ${totalPages}`;

  const btnPrev = document.getElementById('btnPrevSigPage');
  const btnNext = document.getElementById('btnNextSigPage');
  if (btnPrev) btnPrev.disabled = (currentSigPage <= 1);
  if (btnNext) btnNext.disabled = (currentSigPage >= totalPages);
}

function changeSigPage(delta) {
  currentSigPage += delta;
  renderSignalsPage();
}

// ── Commands ───────────────────────────────────────────────
async function loadCommands() {
  const cmds = await apiFetch('/dashboard/commands?limit=30');
  const container = document.getElementById('commandsList');
  const cmdCount = document.getElementById('cmdCount');
  if (cmdCount) cmdCount.textContent = cmds.length;

  if (!cmds.length) {
    if (container) container.innerHTML = '<div class="empty-state">Belum ada perintah ke EA</div>';
    return;
  }

  if (container) {
    container.innerHTML = cmds.map(c => {
      const stateClass = 'state--' + (c.state || '').toLowerCase();
      const time = c.created_at ? new Date(c.created_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' WIB' : '';
      const isManual = c.source_type === 'MANUAL';
      const sourceBadge = isManual ? '<span class="source-pill-manual">👤 MANUAL</span>' : '<span class="source-pill-bot">🤖 BOT</span>';

      return `
      <div class="log-item-row">
        <div class="log-item-left">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">
            ${sourceBadge}
            <span class="log-action-text">${c.action} × ${c.volume} lot (${c.symbol || 'XAUUSD'})</span>
          </div>
          <div class="log-meta-text">Tujuan: ${c.account_name || c.account_alias} · Layer ${c.layer} · TP: ${c.tp_points || '—'} · SL: ${c.sl_points || '—'} · ${time}</div>
        </div>
        <span class="log-state-badge ${stateClass}">${c.state}</span>
      </div>`;
    }).join('');
  }
}

// ── Risk Events ────────────────────────────────────────────
async function loadRiskEvents() {
  const events = await apiFetch('/dashboard/risk-events?unresolved_only=true');
  const container = document.getElementById('riskEventsList');
  const badge = document.getElementById('riskCount');
  const riskBadge = document.getElementById('riskBadge');

  if (badge) badge.textContent = events.length;
  if (riskBadge) riskBadge.textContent = events.length;

  if (!events.length) {
    if (container) container.innerHTML = '<div class="empty-state">Tidak ada risk event aktif ✓</div>';
    return;
  }

  if (container) {
    container.innerHTML = events.map(e => `
    <div class="log-item-row" style="border-left:3px solid var(--red)">
      <div class="log-item-left">
        <div class="log-action-text">${e.event_type} (${e.severity})</div>
        <div class="log-meta-text">${e.detail || ''} · ${e.account_alias || 'system'}</div>
      </div>
      ${e.requires_operator ? '<span class="log-state-badge state--failed">Action Needed</span>' : ''}
    </div>`).join('');
  }
}

// ── System Events ──────────────────────────────────────────
async function loadSystemEvents() {
  const events = await apiFetch('/dashboard/system-events?limit=15');
  const container = document.getElementById('systemEventsList');

  if (!events.length) {
    if (container) container.innerHTML = '<div class="empty-state">Tidak ada log koneksi sistem</div>';
    return;
  }

  if (container) {
    container.innerHTML = events.map(e => {
      const time = e.created_at ? new Date(e.created_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' WIB' : '';
      return `
      <div class="log-item-row">
        <div class="log-item-left">
          <div class="log-action-text">${e.event_type}</div>
          <div class="log-meta-text">${e.detail || 'Event recorded'} · ${time}</div>
        </div>
      </div>`;
    }).join('');
  }
}

// ── Log Filtering ──────────────────────────────────────────
function filterLogs(type) {
  activeLogFilter = type;
  document.querySelectorAll('.chip-filter').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase().includes(type) || (type === 'all' && btn.textContent.toLowerCase().includes('semua')));
  });

  const secSig = document.getElementById('secSignals');
  const secCmd = document.getElementById('secCommands');
  const secRisk = document.getElementById('secRisks');
  const secSys = document.getElementById('secSystems');

  if (secSig) secSig.style.display = (type === 'all' || type === 'signals') ? 'block' : 'none';
  if (secCmd) secCmd.style.display = (type === 'all' || type === 'commands') ? 'block' : 'none';
  if (secRisk) secRisk.style.display = (type === 'all' || type === 'risks') ? 'block' : 'none';
  if (secSys) secSys.style.display = (type === 'all' || type === 'systems') ? 'block' : 'none';
}

// ── Consequential Controls ─────────────────────────────────
function pauseEntries() {
  showModal(
    '⏸️',
    'Jeda Entry Baru (Pause)',
    'Ini akan menghentikan pembukaan posisi baru dari sinyal masuk.\nPosisi yang sedang terbuka di MT5 tetap aktif dan terkelola secara normal.',
    async () => {
      await executeControl('PAUSE_ENTRIES', null, false, 'Jeda Entry');
    }
  );
}

function resumeStrategy() {
  showModal(
    '▶️',
    'Aktifkan Kembali Strategi',
    'Mengaktifkan kembali penerimaan sinyal trading dan pembukaan posisi baru.',
    async () => {
      await executeControl('RESUME_STRATEGY', null, false, 'Strategi Aktif');
    }
  );
}

function emergencyProtection() {
  showModal(
    '🛡️',
    'Proteksi Darurat (Emergency Stop)',
    'PERINGATAN: Mode proteksi darurat akan segera memblokir semua sinyal dan entry baru.\nAnda harus meninjau situasi sebelum mengaktifkannya kembali.',
    async () => {
      await executeControl('EMERGENCY_PROTECTION', null, true, 'Emergency Diaktifkan');
    }
  );
  const btn = document.getElementById('modalConfirmBtn');
  if (btn) btn.style.background = 'var(--red)';
}

function closeCycle(cycleId) {
  showModal(
    '⚠️',
    'Tutup Semua Posisi Siklus',
    `Perintah CLOSE akan dikirim ke EA untuk semua posisi terkelola pada siklus ${cycleId.slice(0, 8)}…\n\nPastikan EA sedang terhubung agar penutupan dieksekusi broker dengan cepat.`,
    async () => {
      await executeControl('CLOSE_CYCLE', cycleId, true, 'Penutupan Posisi');
    }
  );
}

function resumeAfterCritical() {
  showModal(
    '✓',
    'Pemulihan Pasca Darurat',
    'Ini akan menghapus status darurat critical risk dan mengembalikan controller ke status normal.',
    async () => {
      await executeControl('RESUME_AFTER_CRITICAL', null, true, 'Pemulihan Selesai');
    }
  );
}

async function executeControl(action, target, confirm, label) {
  try {
    const payload = { action, confirm };
    if (target) payload.target = target;

    const res = await apiFetch('/control/action', {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    toast(`${label}: ${res.message || 'Sukses'}`, 'success');
    setTimeout(refreshAll, 600);
  } catch (err) {
    toast(`Gagal: ${err.message}`, 'error');
  }
}

// ── Close Positions Controls ──────────────────────────────
function closeAllPositions() {
  showModal(
    '⚠️',
    'Tutup Semua Posisi Terbuka',
    'PERINGATAN: Perintah CLOSE akan segera dikirim ke semua terminal MT5 untuk menutup SEMUA posisi terbuka.\n\nLanjutkan penutupan semua posisi?',
    async () => {
      await executeControl('CLOSE_ALL_POSITIONS', null, true, 'Tutup Semua Posisi');
    }
  );
  const btn = document.getElementById('modalConfirmBtn');
  if (btn) btn.style.background = 'var(--red)';
}

function closePosition(alias, cycleId) {
  const accName = alias === 'account_1' ? 'Akun 1 (Primary)' : (alias === 'account_2' ? 'Akun 2 (Secondary)' : 'Akun 3 (Tertiary)');
  const title = cycleId ? `Tutup Posisi Siklus (${cycleId.slice(0, 8)}…)` : `Tutup Posisi ${accName}`;
  showModal(
    '✕',
    'Konfirmasi Tutup Posisi',
    `Kirim perintah penutupan posisi pada [${accName}] sekarang?`,
    async () => {
      if (cycleId) {
        await executeControl('CLOSE_CYCLE', cycleId, true, `Tutup Posisi ${accName}`);
      } else {
        await executeControl('CLOSE_ALL_POSITIONS', null, true, `Tutup Posisi ${accName}`);
      }
    }
  );
  const btn = document.getElementById('modalConfirmBtn');
  if (btn) btn.style.background = 'var(--red)';
}

// ── Manual Order Entry ─────────────────────────────────────
async function sendManualOrder(direction, prefix = 'order') {
  const dir = direction.toUpperCase();
  const icon = dir === 'BUY' ? '🟢' : '🔴';
  const color = dir === 'BUY' ? 'var(--green)' : 'var(--red)';

  const lotInput = document.getElementById(prefix + 'LotInput');
  const tpInput  = document.getElementById(prefix + 'TpInput');
  const slInput  = document.getElementById(prefix + 'SlInput');

  const volume = lotInput ? Math.max(0.01, parseFloat(lotInput.value) || 0.01) : 0.01;
  const tpPoints = tpInput && tpInput.value !== '' ? parseInt(tpInput.value, 10) : 300;
  const slPoints = slInput && slInput.value !== '' ? parseInt(slInput.value, 10) : 500;

  const tpDesc = tpPoints > 0 ? `${tpPoints} Poin (${tpPoints / 10} pips)` : 'Tanpa TP';
  const slDesc = slPoints > 0 ? `${slPoints} Poin (${slPoints / 10} pips)` : 'Tanpa SL';

  showModal(
    icon,
    `Konfirmasi Manual ${dir} Order`,
    `Eksekusi order ${dir} XAUUSD pada Akun 1 (Primary):\n\n• Target: Akun 1 (Primary - Stage 1)\n• Simbol: XAUUSD\n• Volume: ${volume.toFixed(2)} Lot\n• Take Profit: ${tpDesc}\n• Stop Loss: ${slDesc}\n\nLanjutkan eksekusi ke broker?`,
    async () => {
      try {
        toast(`Mengirim order ${dir} ${volume} lot...`, 'info');
        const res = await apiFetch('/signal', {
          method: 'POST',
          body: JSON.stringify({
            strategy_id: 'stma-xau-v1',
            symbol: 'XAUUSD',
            direction: dir,
            source: 'dashboard-manual',
            price: null,
            payload: {
              volume: volume,
              tp_points: tpPoints > 0 ? tpPoints : null,
              sl_points: slPoints > 0 ? slPoints : null,
            }
          }),
        });

        if (res.status === 'ACCEPTED') {
          toast(`✓ Order ${dir} Berhasil Diterima! Perintah dikirim ke MT5 Akun 1.`, 'success');
        } else if (res.status === 'RISK_DENIED') {
          toast(`Ditolak Risk Engine: ${res.message}`, 'error');
        } else {
          toast(`Sinyal ${res.status}: ${res.message || 'Tidak dapat diproses'}`, 'warning');
        }
        setTimeout(refreshAll, 600);
      } catch (err) {
        toast(`Gagal: ${err.message}`, 'error');
      }
    }
  );

  const confirmBtn = document.getElementById('modalConfirmBtn');
  if (confirmBtn) {
    confirmBtn.style.background = color;
  }
}

// ── Settings Save ──────────────────────────────────────────
function saveSettings() {
  const urlInput = document.getElementById('serverUrlInput');
  const tokenInput = document.getElementById('authTokenInput');

  if (urlInput && urlInput.value.trim()) {
    API = urlInput.value.trim().replace(/\/+$/, '');
    localStorage.setItem('STMA_API_URL', API);
  }

  if (tokenInput && tokenInput.value.trim()) {
    AUTH_TOKEN = tokenInput.value.trim();
    localStorage.setItem('STMA_AUTH_TOKEN', AUTH_TOKEN);
  }

  toast('Konfigurasi disimpan. Menghubungkan ulang...', 'info');
  refreshAll();
}

function populateSettings() {
  const urlInput = document.getElementById('serverUrlInput');
  const tokenInput = document.getElementById('authTokenInput');
  if (urlInput) urlInput.value = API;
  if (tokenInput) tokenInput.value = AUTH_TOKEN;
}

// ── Mobile Bottom Sheet Modal ──────────────────────────────
function showModal(icon, title, msg, onConfirm) {
  const modalOverlay = document.getElementById('modalOverlay');
  const modalIcon = document.getElementById('modalIcon');
  const modalTitle = document.getElementById('modalTitle');
  const modalMsg = document.getElementById('modalMsg');
  const confirmBtn = document.getElementById('modalConfirmBtn');

  if (modalIcon) modalIcon.textContent = icon;
  if (modalTitle) modalTitle.textContent = title;
  if (modalMsg) modalMsg.textContent = msg;
  if (confirmBtn) confirmBtn.style.background = '';

  pendingAction = onConfirm;
  if (modalOverlay) modalOverlay.style.display = 'flex';
}

function closeModal() {
  const modalOverlay = document.getElementById('modalOverlay');
  if (modalOverlay) modalOverlay.style.display = 'none';
  pendingAction = null;
}

const confirmBtn = document.getElementById('modalConfirmBtn');
if (confirmBtn) {
  confirmBtn.addEventListener('click', async () => {
    if (typeof pendingAction === 'function') {
      const act = pendingAction;
      closeModal();
      try {
        await act();
      } catch (err) {
        console.error('Action error:', err);
      }
    }
  });
}

const modalOverlay = document.getElementById('modalOverlay');
if (modalOverlay) {
  modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeModal();
  });
}

// ── Toast Notifications ────────────────────────────────────
function toast(message, type = 'info') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  el.textContent = message;
  container.appendChild(el);

  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(-6px)';
    el.style.transition = 'all 0.3s ease';
  }, 3000);

  setTimeout(() => el.remove(), 3350);
}

// ── Init ───────────────────────────────────────────────────
populateSettings();
refreshAll();
refreshTimer = setInterval(refreshAll, REFRESH_INTERVAL);
