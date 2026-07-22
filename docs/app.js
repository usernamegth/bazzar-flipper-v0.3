const statusDot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const matchCount = document.getElementById('match-count');
const scannedCount = document.getElementById('scanned-count');
const countdownEl = document.getElementById('countdown');
const ledgerBody = document.getElementById('ledger-body');
const errorBanner = document.getElementById('error-banner');
const volumeThresholdEl = document.getElementById('volume-threshold');
const spreadThresholdEl = document.getElementById('spread-threshold');
const windowDaysEl = document.getElementById('window-days');
const pollIntervalEl = document.getElementById('poll-interval');

let nextSyncAt = null;

function fmtNumber(n) {
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function fmtCoins(n) {
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function manipulationCell(m) {
  if (!m) return '';
  const parts = [];
  if (m.buy_change_pct !== null && m.buy_change_pct > 100) {
    parts.push(`buy: ${fmtCoins(m.prior_buy_price)} &rarr; now`);
  }
  if (m.sell_change_pct !== null && m.sell_change_pct > 100) {
    parts.push(`sell: ${fmtCoins(m.prior_sell_price)} &rarr; now`);
  }
  return `
    <span class="alert-badge">likely manipulation</span>
    <div class="alert-detail">${parts.join(' &middot; ')} (~${m.reference_minutes_ago}m ago)</div>
  `;
}

function renderRows(items) {
  if (!items || items.length === 0) {
    ledgerBody.innerHTML = '<tr class="empty-row"><td colspan="9">No items currently clear both thresholds. Check back after the next sync.</td></tr>';
    return;
  }

  const maxSpread = Math.max(...items.map(i => i.spread_pct), 1);

  ledgerBody.innerHTML = items.map((item, idx) => {
    const gaugePct = Math.min(100, (item.spread_pct / maxSpread) * 100);
    const rowClass = item.manipulation ? ' class="row-manipulation"' : '';
    return `
      <tr${rowClass}>
        <td class="col-rank">${idx + 1}</td>
        <td class="col-name">${item.name}</td>
        <td class="col-num">${fmtCoins(item.avg_price)}</td>
        <td class="col-num">${fmtCoins(item.sell_price)}</td>
        <td class="col-num">${fmtCoins(item.buy_price)}</td>
        <td class="col-spread">
          <div class="spread-cell">
            <span class="spread-value">${item.spread_pct.toFixed(1)}%</span>
            <span class="spread-gauge"><span class="spread-gauge-fill" style="width:${gaugePct}%"></span></span>
          </div>
        </td>
        <td class="col-num">${fmtNumber(item.est_buy_volume)}</td>
        <td class="col-num">${fmtNumber(item.est_sell_volume)}</td>
        <td class="col-alert">${manipulationCell(item.manipulation)}</td>
      </tr>
    `;
  }).join('');
}

async function refresh() {
  try {
    // Cache-bust: GitHub Pages / browsers will otherwise happily serve a stale copy.
    const res = await fetch(`data.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('not published yet');
    const data = await res.json();

    const cfg = data.config || {};
    const intervalMin = cfg.poll_interval_minutes || 30;
    volumeThresholdEl.textContent = fmtNumber(cfg.volume_threshold || 0);
    spreadThresholdEl.textContent = cfg.spread_threshold_pct ?? '–';
    windowDaysEl.textContent = cfg.window_days ?? '–';
    pollIntervalEl.textContent = intervalMin;

    const lastUpdated = data.last_updated ? new Date(data.last_updated) : null;

    if (data.error) {
      errorBanner.hidden = false;
      errorBanner.textContent = `Last check failed: ${data.error} (showing most recent good data)`;
    } else {
      errorBanner.hidden = true;
    }

    statusDot.className = 'dot live';
    statusText.textContent = lastUpdated
      ? `Synced ${lastUpdated.toLocaleString()}`
      : 'Waiting for first sync';
    nextSyncAt = (lastUpdated ? lastUpdated.getTime() : Date.now()) + intervalMin * 60000;

    matchCount.textContent = data.items ? data.items.length : '0';
    scannedCount.textContent = data.products_scanned ? fmtNumber(data.products_scanned) : '0';
    renderRows(data.items);
  } catch (err) {
    statusDot.className = 'dot down';
    statusText.textContent = 'No data yet';
    ledgerBody.innerHTML = '<tr class="empty-row"><td colspan="9">' +
      'data.json hasn\'t been published yet. Trigger the "Update Bazaar Data" workflow ' +
      'manually from the Actions tab, or wait for its first scheduled run.</td></tr>';
  }
}

function tickCountdown() {
  if (!nextSyncAt) { countdownEl.textContent = '–'; return; }

  const diffMs = nextSyncAt - Date.now();
  if (diffMs <= 0) {
    // The expected update time has passed but data.json hasn't changed yet.
    // GitHub's scheduled runs aren't perfectly punctual (they can run a few
    // minutes late, especially during busy periods), so this is normal -
    // show that we're actively waiting rather than freezing at "0:00".
    countdownEl.textContent = 'checking…';
    return;
  }

  const remaining = Math.round(diffMs / 1000);
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  countdownEl.textContent = `${m}:${s.toString().padStart(2, '0')}`;
}

refresh();
setInterval(refresh, 20000);   // check for a fresh data.json every 20s (cheap - it's a static file) so a late update is picked up quickly
setInterval(tickCountdown, 1000);
