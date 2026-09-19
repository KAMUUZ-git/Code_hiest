'use strict';

// Guard against double-loading (e.g. cached script + static re-serve)
if (window.__DT_LOADED__) {
  console.warn('[dt] app.js already loaded — skipping re-init');
  throw new Error('app.js double-load guard');
}
window.__DT_LOADED__ = true;

/* =========================================================
   CAMPUS DIGITAL TWIN v2 — FRONTEND COMMAND CENTER
   ---------------------------------------------------------
   1. CONFIG          — tunables (WS endpoint, tiers, map)
   2. STATE           — campusData store + UI state
   3. MAP SETUP       — Leaflet init + dark basemap
   4. MARKERS         — radar-blip DivIcons + zone circles
   5. SIDEBAR         — list view, detail view + Chart.js
   6. STREAM          — WebSocket client (no polling loops)
   7. PREDICTIONS HUD — 60-min breach queue renderer
   8. ANOMALY BANNER  — cascading-event narration
   9. TOASTS          — connection + alert notifications
  10. UI ACTIONS      — collapse, filters, clock, reset
   ========================================================= */

/* =========================================================
   1. CONFIG
   ========================================================= */
const CONFIG = {
  // ── IIT Delhi main campus (Hauz Khas, New Delhi) ────────────
  CAMPUS_CENTER: [28.5450, 77.1926],
  CAMPUS_ZOOM: 17,

  // Lock the map to IIT Delhi: users can pan INSIDE the fence but
  // never out to the rest of Delhi. Bounds are generous (include the
  // full campus + a little breathing room) and viscosity 1.0 makes
  // the edge feel like a wall, not a trampoline.
  MAP_MAX_BOUNDS: [
    [28.5385, 77.1830],   // south-west (DLC/hostels side)
    [28.5525, 77.2010],   // north-east (JHU / Gate margins)
  ],
  MAP_MIN_ZOOM: 15,       // can't zoom out past campus context
  DETAIL_ZOOM: 18,

  TILE_URL: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  TILE_ATTRIBUTION:
    '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
  MAX_ZOOM: 19,

  // ── Transport: WebSocket-first, REST fallback for history ──
  WS_URL: 'ws://localhost:8000/ws',
  API_BASE: 'http://localhost:8000',
  WS_RETRY_MS: 2500,          // exponential-ish backoff base
  WS_RETRY_MAX_MS: 15000,
  HISTORY_WINDOW_MIN: 60,

  TIERS: {
    GREEN:  { max: 60,  color: '#10B981', label: 'Optimal' },
    AMBER:  { max: 85,  color: '#F59E0B', label: 'Moderate' },
    RED:    { max: 101, color: '#EF4444', label: 'Near Capacity' },
  },

  CHART_WINDOW_POINTS: 30,    // points visible on the live trend chart
};

function tierFor(pct) {
  if (pct == null) return 'GREEN';
  if (pct < CONFIG.TIERS.GREEN.max) return 'GREEN';
  if (pct < CONFIG.TIERS.AMBER.max) return 'AMBER';
  return 'RED';
}

function isFacilityCritical(f) {
  if (!f) return false;
  if (tierFor(f.occupancy_pct) === 'RED') return true;
  return (f.alerts ?? []).some(a => /critical|fault|danger/i.test(a.type ?? ''));
}

/* =========================================================
   2. STATE
   ========================================================= */
const state = {
  map: null,
  campusData: { facilities: [], lastUpdated: null },
  facilityById: new Map(),
  markers: new Map(),
  zones: new Map(),
  layerGroup: null,

  activeFilter: 'all',
  selectedId: null,
  sidebarCollapsed: false,
  view: 'list',
  wasOnline: null,

  // ── WS lifecycle ──
  ws: null,
  wsAttempt: 0,
  wsManualClose: false,
  wsPingTimer: null,

  // ── Chart instance (detail view) ──
  chart: null,
  chartFacilityId: null,
  chartLoading: false,
};

/* =========================================================
   3. MAP SETUP
   ========================================================= */
function initMap() {
  const map = L.map('map', {
    center: CONFIG.CAMPUS_CENTER,
    zoom: CONFIG.CAMPUS_ZOOM,
    zoomControl: false,
    zoomSnap: 0.25,
    zoomDelta: 0.5,
    wheelPxPerZoomLevel: 90,
    attributionControl: false,
    // ── IIT Delhi fence ──
    maxBounds: CONFIG.MAP_MAX_BOUNDS,
    maxBoundsViscosity: 1.0,
    minZoom: CONFIG.MAP_MIN_ZOOM,
  });

  L.tileLayer(CONFIG.TILE_URL, {
    attribution: CONFIG.TILE_ATTRIBUTION,
    maxZoom: CONFIG.MAX_ZOOM,
    updateWhenIdle: false,
    keepBuffer: 3,
  }).addTo(map);

  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.control.attribution({ position: 'bottomright', prefix: false })
    .addAttribution(CONFIG.TILE_ATTRIBUTION).addTo(map);

  map.on('mousemove', (e) => updateReadout(e.latlng));
  map.on('mouseout', () => updateReadout(null));

  state.map = map;
}

function updateReadout(latlng) {
  const el = document.getElementById('latlng-readout');
  if (!el) return;
  el.textContent = latlng
    ? `${latlng.lat.toFixed(4)}, ${latlng.lng.toFixed(4)}`
    : `${CONFIG.CAMPUS_CENTER[0].toFixed(4)}, ${CONFIG.CAMPUS_CENTER[1].toFixed(4)}`;
}

/* =========================================================
   4. MARKERS — radar blips + soft zones
   ========================================================= */
function markerIcon(facility) {
  const tier = tierFor(facility.occupancy_pct);
  const color = CONFIG.TIERS[tier].color;
  const size = 18;
  const critical = isFacilityCritical(facility) ? ' is-critical' : '';
  return L.divIcon({
    className: 'dt-marker',
    html: `
      <div class="dt-marker-inner${critical}" style="--c:${color}; --s:${size}px">
        <div class="dt-marker-ring"></div>
        <div class="dt-marker-dot"></div>
      </div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function buildFacilityLayers() {
  if (state.layerGroup) state.layerGroup.remove();
  state.markers.clear();
  state.zones.clear();

  state.layerGroup = L.featureGroup().addTo(state.map);

  for (const facility of state.campusData.facilities) {
    if (!Array.isArray(facility.coords) || facility.coords.length !== 2) continue;

    const tier = tierFor(facility.occupancy_pct);
    const color = CONFIG.TIERS[tier].color;
    const zoneM = facility.zone_m ?? 60;   // per-facility footprint from the registry

    const zone = L.circle(facility.coords, {
      radius: zoneM, color, weight: 1.5, opacity: 0.55,
      fillColor: color, fillOpacity: 0.12, interactive: false,
    }).addTo(state.layerGroup);

    // Big landmarks (library, hostels, parking…) carry a permanent
    // glass name label so the map reads like a campus wayfinding board.
    const marker = L.marker(facility.coords, {
      icon: markerIcon(facility),
      title: facility.name,
      riseOnHover: true,
    });
    if (zoneM >= 90) {
      marker.bindTooltip(facility.name, {
        permanent: true, direction: 'top', offset: [0, -10],
        className: 'dt-map-label', opacity: 1,
      });
    } else {
      marker.bindTooltip(`${facility.name} · ${Math.round(facility.occupancy_pct)}%`, {
        direction: 'top', offset: [0, -12], className: 'dt-tooltip',
      });
    }
    marker.on('click', () => selectFacility(facility.id, { fly: true }));
    marker.addTo(state.layerGroup);

    state.markers.set(facility.id, marker);
    state.zones.set(facility.id, zone);
  }
}

/** Re-tint an existing marker/zone in place (no layer rebuild). */
function restyleFacilityLayers(facility) {
  const tier = tierFor(facility.occupancy_pct);
  const color = CONFIG.TIERS[tier].color;
  const critical = isFacilityCritical(facility);

  const marker = state.markers.get(facility.id);
  if (marker) {
    marker.setIcon(markerIcon(facility));
    // Permanent labels (big zones) show only the name — don't clobber
    // them with the hover-style occupancy tooltip.
    if ((facility.zone_m ?? 60) < 90) {
      marker.setTooltipContent(`${facility.name} · ${Math.round(facility.occupancy_pct)}%`);
    }
    const el = marker.getElement();
    if (el) {
      el.classList.toggle('is-selected', facility.id === state.selectedId);
      const match = state.activeFilter === 'all' ||
                    state.facilityById.get(facility.id)?.category === state.activeFilter;
      el.style.opacity = match ? '1' : '0.25';
    }
  }

  const zone = state.zones.get(facility.id);
  if (zone) {
    zone.setStyle({
      color, fillColor: color,
      weight: critical ? 2.5 : 1.5,
      opacity: critical ? 0.9 : 0.55,
      fillOpacity: critical ? 0.22 : 0.12,
    });
  }
}

/* =========================================================
   5. SIDEBAR — list, detail + Chart.js live trend
   ========================================================= */
function syncSidebarViews() {
  const hasData = state.campusData.facilities.length > 0;
  document.getElementById('empty-state').classList.toggle('hidden', hasData);
  document.getElementById('facility-list').classList.toggle('hidden', state.view !== 'list');
  document.getElementById('facility-detail').classList.toggle('hidden', state.view !== 'detail');
}

function filteredFacilities() {
  if (state.activeFilter === 'all') return state.campusData.facilities;
  return state.campusData.facilities.filter(f => f.category === state.activeFilter);
}

function renderFacilityList() {
  const listEl = document.getElementById('facility-list');
  const facilities = filteredFacilities();

  listEl.innerHTML = facilities.map(f => {
    const tier = tierFor(f.occupancy_pct);
    const t = CONFIG.TIERS[tier];
    const critical = isFacilityCritical(f);
    const pct = Math.round(f.occupancy_pct);
    const remaining = f.type === 'parking'
      ? `${Math.max(0, f.capacity - f.occupancy)} slots free`
      : `${f.occupancy} / ${f.capacity} occupied`;
    const alertBadge = (f.alerts?.length ?? 0) > 0
      ? `<span class="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500/20 px-1 text-[9px] font-bold text-red-300 ring-1 ring-red-500/40">${f.alerts.length}</span>`
      : '';
    return `
      <button data-id="${f.id}" class="facility-card w-full text-left rounded-xl border p-3.5 transition
                     ${state.selectedId === f.id
                       ? 'border-sky-500/50 bg-sky-500/10'
                       : 'border-slate-800 bg-surface-raised/70 hover:bg-surface-hover/60 hover:border-slate-700'}
                     ${critical ? 'ring-2 ring-red-500/80 border-red-500/60' : ''}">
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <p class="truncate text-[15px] font-semibold text-slate-100">${f.name}${alertBadge}</p>
            <p class="mt-0.5 text-xs text-slate-500">${f.type === 'parking' ? 'Parking' : f.type} · ${f.hours ?? '—'}</p>
          </div>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-xs font-bold"
                style="color:${t.color}; background:${t.color}1f; border:1px solid ${t.color}55">
            ${pct}%
          </span>
        </div>
        <div class="mt-2.5 h-2 overflow-hidden rounded-full bg-slate-800">
          <div class="h-full rounded-full dt-bar-fill" style="width:${pct}%; background:${t.color}"></div>
        </div>
        <p class="mt-1.5 text-[11px] font-semibold" style="color:${t.color}">${t.label} · ${remaining}</p>
      </button>`;
  }).join('');

  listEl.querySelectorAll('.facility-card').forEach(card => {
    card.addEventListener('click', () => selectFacility(card.dataset.id, { fly: true }));
  });

  syncSidebarViews();
}

/* ── Detail view + Chart.js live line graph ────────────────── */

function renderFacilityDetail() {
  const el = document.getElementById('facility-detail');
  const f = state.facilityById.get(state.selectedId);
  if (!f) { state.view = 'list'; syncSidebarViews(); return; }

  const tier = tierFor(f.occupancy_pct);
  const t = CONFIG.TIERS[tier];
  const critical = isFacilityCritical(f);
  const isParking = f.type === 'parking';
  const pct = Math.round(f.occupancy_pct);
  const statusLine = isParking
    ? `<span class="font-semibold" style="color:${t.color}">Parking: ${Math.max(0, f.capacity - f.occupancy)} slots remaining</span>`
    : `<span class="font-semibold" style="color:${t.color}">Open</span> • ${f.occupancy} / ${f.capacity} Seats Occupied`;

  el.innerHTML = `
    <button id="btn-back" class="mb-3 flex items-center gap-1.5 text-[11px] font-semibold text-slate-400 transition hover:text-sky-400">
      <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7" />
      </svg>
      All Facilities
    </button>

    <div class="rounded-xl border border-slate-800 bg-surface-raised/80 p-4
                ${critical ? 'ring-2 ring-red-500/80 animate-pulse' : ''}">
      <div class="flex items-start justify-between gap-2">
        <div>
          <h3 class="text-base font-bold text-slate-100">${f.name}</h3>
          <p class="mt-0.5 text-xs text-slate-500">${f.type} · ${f.hours ?? '—'}</p>
        </div>
        <span class="rounded-full px-2 py-0.5 text-xs font-bold"
              style="color:${t.color}; background:${t.color}1f; border:1px solid ${t.color}55">
          ${t.label}
        </span>
      </div>

      <div class="mt-4">
        <div class="flex items-center justify-between text-xs">
          <span class="font-semibold uppercase tracking-wider text-slate-400">Live Occupancy</span>
          <span class="font-mono font-bold" style="color:${t.color}">${pct}%</span>
        </div>
        <div class="mt-1.5 h-2.5 overflow-hidden rounded-full bg-slate-800 ring-1 ring-slate-700/50">
          <div class="h-full rounded-full dt-bar-fill" style="width:${pct}%; background:${t.color};
                box-shadow: 0 0 12px ${t.color}66"></div>
        </div>
        <p class="mt-2 text-sm text-slate-200">${statusLine}</p>
      </div>

      <!-- Live trend graph (Chart.js) -->
      <div class="mt-4">
        <div class="flex items-center justify-between">
          <p class="text-[10px] font-bold uppercase tracking-widest text-slate-400">Traffic Trend · Last 60 min</p>
          <span id="chart-status" class="font-mono text-[10px] text-slate-500">loading…</span>
        </div>
        <div class="chart-box mt-2"><canvas id="trend-chart"></canvas></div>
      </div>

      <!-- Activity -->
      <div class="mt-4 flex items-start gap-2 rounded-lg border border-slate-800 bg-surface/60 p-2.5">
        <span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md"
              style="background:${t.color}1f; color:${t.color}">
          <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
          </svg>
        </span>
        <div class="min-w-0">
          <p class="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Recent Activity</p>
          <p class="mt-0.5 text-xs font-medium text-slate-200">${f.activity ?? 'Operational'}</p>
        </div>
      </div>

      <!-- Alerts -->
      ${(f.alerts?.length ?? 0) > 0 ? `
        <div class="mt-3 space-y-1.5">
          ${f.alerts.slice(-3).reverse().map(a => `
            <div class="flex items-start gap-2 rounded-lg border px-2.5 py-2"
                 style="border-color: rgba(239,68,68,.3); background: rgba(239,68,68,.06)">
              <span class="text-xs">🔔</span>
              <p class="text-[11px] leading-snug text-slate-300">
                <span class="font-bold text-red-300">[${a.type}]</span> ${a.message}
              </p>
            </div>`).join('')}
        </div>` : ''}

      <p class="mt-3 text-center text-[11px] text-slate-500">
        Updated ${new Date(state.campusData.lastUpdated ?? Date.now()).toLocaleTimeString([], { hour12: false })}
        · <span class="font-mono">ID ${f.id}</span>
      </p>
    </div>`;

  el.querySelector('#btn-back').addEventListener('click', () => {
    state.view = 'list';
    state.selectedId = null;
    destroyChart();
    setSelectedMarker();
    renderFacilityList();
  });

  initTrendChart(f);
  syncSidebarViews();
}

/** Create the Chart.js line graph and hydrate it from /api/history. */
function initTrendChart(facility) {
  destroyChart();

  const ctx = document.getElementById('trend-chart');
  if (!ctx) return;
  const statusEl = document.getElementById('chart-status');
  const color = CONFIG.TIERS[tierFor(facility.occupancy_pct)].color;

  state.chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Occupancy %',
        data: [],
        borderColor: color,
        backgroundColor: `${color}22`,
        borderWidth: 2,
        fill: true,
        tension: 0.35,
        pointRadius: 0,
        pointHitRadius: 12,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false } },
      scales: {
        x: {
          ticks: { color: '#64748b', font: { size: 9, family: 'JetBrains Mono' }, maxTicksLimit: 6 },
          grid: { color: 'rgba(148,163,184,.07)' },
        },
        y: {
          min: 0, max: 100,
          ticks: { color: '#64748b', font: { size: 9, family: 'JetBrains Mono' },
                   callback: v => `${v}%` },
          grid: { color: 'rgba(148,163,184,.07)' },
        },
      },
    },
  });
  state.chartFacilityId = facility.id;

  // Hydrate from the historical record (SQLite) — then the WS stream
  // appends live points on every telemetry event.
  state.chartLoading = true;
  fetch(`${CONFIG.API_BASE}/api/history/${facility.id}?minutes=${CONFIG.HISTORY_WINDOW_MIN}&limit=120`)
    .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then(payload => {
      if (state.chartFacilityId !== facility.id || !state.chart) return;
      const points = (payload.points ?? []).slice(-CONFIG.CHART_WINDOW_POINTS);
      state.chart.data.labels = points.map(p => formatTrendLabel(p.ts));
      state.chart.data.datasets[0].data = points.map(p => Math.round(p.occupancy_pct));
      state.chart.update();
      if (statusEl) statusEl.textContent = `${points.length} pts`;
    })
    .catch(() => { if (statusEl) statusEl.textContent = 'history unavailable'; })
    .finally(() => { state.chartLoading = false; });
}

/** Live point push from the WS telemetry handler. */
function pushChartPoint(facility) {
  if (!state.chart || state.chartFacilityId !== facility.id) return;
  const chart = state.chart;
  chart.data.labels.push(formatTrendLabel(new Date().toISOString()));
  chart.data.datasets[0].data.push(Math.round(facility.occupancy_pct));
  if (chart.data.labels.length > CONFIG.CHART_WINDOW_POINTS) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
  chart.update('none');   // no animation on live ticks — smooth stream
  const statusEl = document.getElementById('chart-status');
  if (statusEl) statusEl.textContent = `${chart.data.labels.length} pts · live`;
}

function destroyChart() {
  if (state.chart) { state.chart.destroy(); state.chart = null; }
  state.chartFacilityId = null;
}

function formatTrendLabel(ts) {
  const d = ts ? new Date(ts) : new Date();
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/* ── Selection flow ────────────────────────────────────────── */

function setSelectedMarker() {
  for (const [id, marker] of state.markers) {
    const el = marker.getElement();
    if (el) el.classList.toggle('is-selected', id === state.selectedId);
  }
}

function selectFacility(id, { fly = false } = {}) {
  const f = state.facilityById.get(id);
  if (!f) return;
  state.selectedId = id;
  state.view = 'detail';
  renderFacilityDetail();
  setSelectedMarker();
  if (fly && state.map && Array.isArray(f.coords)) {
    state.map.flyTo(f.coords, CONFIG.DETAIL_ZOOM, { duration: 1.1 });
  }
}

/* =========================================================
   6. STREAM — WebSocket client (zero polling)
   ========================================================= */

function connectWebSocket() {
  if (state.wsManualClose) return;

  let ws;
  try {
    ws = new WebSocket(CONFIG.WS_URL);
  } catch (err) {
    scheduleReconnect();
    return;
  }
  state.ws = ws;

  ws.onopen = () => {
    state.wsAttempt = 0;
    setConnectionStatus(true);
    // Keep the socket warm + measure liveness.
    clearInterval(state.wsPingTimer);
    state.wsPingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'ping' }));
    }, 15000);
  };

  ws.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }
    handleStreamMessage(msg);
  };

  ws.onclose = () => {
    clearInterval(state.wsPingTimer);
    setConnectionStatus(false);
    scheduleReconnect();
  };

  ws.onerror = () => { try { ws.close(); } catch { /* noop */ } };
}

function scheduleReconnect() {
  if (state.wsManualClose) return;
  const delay = Math.min(CONFIG.WS_RETRY_MS * Math.pow(1.6, state.wsAttempt++), CONFIG.WS_RETRY_MAX_MS);
  setTimeout(connectWebSocket, delay);
}

/** Route one server message into the store + UI. */
function handleStreamMessage(msg) {
  switch (msg.type) {
    case 'snapshot':
      seedFromPayload(msg.facilities ?? []);
      setConnectionStatus(true);
      break;

    case 'telemetry':
      applyTelemetry(msg.facility);
      break;

    case 'alert': {
      // The telemetry event for this facility carries the alert list;
      // if it raced ahead of us, refresh from the event payload.
      const f = state.facilityById.get(msg.facility_id);
      if (f && msg.alert) {
        f.alerts = [...(f.alerts ?? []), msg.alert].slice(-20);
        updateFacilitySurfaces(f);
        if (msg.alert.severity === 'critical') {
          showToast(`[${msg.alert.type}] ${msg.alert.message}`, 'error');
        }
      }
      break;
    }

    case 'anomaly':
      showAnomalyBanner(msg.title ?? 'Anomaly Detected', msg.narrative ?? '', msg.title ?? '');
      break;

    case 'predictions':
      renderPredictions(msg.breach_queue ?? [], msg.model_ready !== false);
      break;

    case 'reset':
      seedFromPayload(msg.facilities ?? []);
      showToast('Campus reset to baseline', 'success');
      break;

    case 'pong':
      // Liveness ack — nothing to render.
      break;

    default:
      break;
  }
}

/** First snapshot: build the whole board. */
function seedFromPayload(facilities) {
  state.campusData.facilities = facilities.map(f => ({
    id: f.id,
    name: f.name,
    type: f.type ?? 'building',
    category: f.category ?? 'academic',
    coords: f.coords ?? null,
    hours: f.hours ?? '—',
    capacity: f.capacity ?? 100,
    occupancy: f.occupancy ?? 0,
    occupancy_pct: f.occupancy_pct ?? 0,
    status: f.status ?? 'GREEN',
    activity: f.activity ?? 'Operational',
    alerts: f.alerts ?? [],
  }));
  state.campusData.lastUpdated = new Date().toISOString();
  state.facilityById = new Map(state.campusData.facilities.map(f => [f.id, f]));

  buildFacilityLayers();
  renderFacilityList();
  if (state.view === 'detail' && state.selectedId) {
    if (state.facilityById.has(state.selectedId)) renderFacilityDetail();
    else { state.view = 'list'; state.selectedId = null; syncSidebarViews(); }
  }
}

/** Delta telemetry: mutate + re-render only affected surfaces. */
function applyTelemetry(facility) {
  if (!facility?.id) return;
  const prevTier = state.facilityById.get(facility.id)?.status;
  const existing = state.facilityById.get(facility.id);

  const merged = {
    ...(existing ?? {}),
    ...facility,
    occupancy: facility.occupancy ?? existing?.occupancy ?? 0,
    occupancy_pct: facility.occupancy_pct ?? existing?.occupancy_pct ?? 0,
    alerts: facility.alerts ?? existing?.alerts ?? [],
  };
  state.facilityById.set(facility.id, merged);
  const idx = state.campusData.facilities.findIndex(f => f.id === facility.id);
  if (idx >= 0) state.campusData.facilities[idx] = merged;
  else state.campusData.facilities.push(merged);

  state.campusData.lastUpdated = facility.last_updated ?? new Date().toISOString();

  updateFacilitySurfaces(merged, prevTier);

  if (state.view === 'detail' && state.selectedId === facility.id) {
    renderFacilityDetail();          // rebuilds cards/alerts
    pushChartPoint(merged);          // live point onto Chart.js
  }
}

/** Shared per-facility re-render: marker, zone, list card flash. */
function updateFacilitySurfaces(f, prevStatus) {
  if (!Array.isArray(f.coords)) { renderFacilityList(); return; }
  if (state.markers.has(f.id)) {
    restyleFacilityLayers(f);
  } else {
    buildFacilityLayers();           // first time we have coords
  }

  const newTier = tierFor(f.occupancy_pct);
  if (prevStatus && prevStatus !== newTier) {
    const card = document.querySelector(`.facility-card[data-id="${f.id}"]`);
    if (card) {
      card.classList.remove('dt-flash');
      void card.offsetWidth;         // restart CSS animation
      card.classList.add('dt-flash');
    }
  }
  renderFacilityList();
}

/* ── Connection status UI ──────────────────────────────────── */

const CONN_UI = {
  connecting: {
    frame: ['border-amber-500/30', 'bg-amber-500/10', 'text-amber-400'],
    chip: ['#fbbf24', 'WS CONNECTING…'],
  },
  online: {
    label: 'System Online • Live Stream',
    frame: ['border-emerald-500/30', 'bg-emerald-500/10', 'text-emerald-400'],
    dot: 'bg-emerald-500', ping: 'bg-emerald-400',
    chip: ['#10B981', 'WS LIVE'],
  },
  offline: {
    label: 'Connection Lost',
    frame: ['border-red-500/40', 'bg-red-500/10', 'text-red-400'],
    dot: 'bg-red-500', ping: 'bg-red-400',
    chip: ['#EF4444', 'WS OFFLINE · RETRYING'],
  },
};

function setConnectionStatus(online) {
  const ui = online ? CONN_UI.online : CONN_UI.offline;
  const badge = document.getElementById('status-badge');

  badge.classList.remove(...CONN_UI.connecting.frame, ...CONN_UI.online.frame, ...CONN_UI.offline.frame);
  badge.classList.add(...ui.frame);
  document.getElementById('conn-dot').className =
    `relative inline-flex h-2 w-2 rounded-full ${ui.dot}`;
  document.getElementById('conn-ping').className =
    `absolute inline-flex h-full w-full animate-ping rounded-full opacity-75 ${ui.ping}`;
  document.getElementById('status-label').textContent = ui.label;

  document.getElementById('conn-chip-dot').style.background = ui.chip[0];
  const chipText = document.getElementById('conn-chip-text');
  chipText.textContent = ui.chip[1];
  chipText.style.color = ui.chip[0];

  document.getElementById('empty-state-title').textContent =
    online ? 'Waiting for telemetry…' : 'Backend Unreachable';
  const emptyDot = document.getElementById('empty-state-dot');
  emptyDot.classList.toggle('bg-amber-400', online);
  emptyDot.classList.toggle('bg-red-400', !online);
  document.getElementById('empty-state-label').textContent =
    online ? 'Stream connected' : 'Reconnecting with backoff…';

  // Toast only on real transitions (not cold start)
  if (state.wasOnline === false && online) {
    showToast('Live Stream Restored — WebSocket Connected', 'success');
  }
  if (state.wasOnline === true && !online) {
    showToast('Live Stream Interrupted. Reconnecting…', 'error');
  }
  state.wasOnline = online;
}

/* =========================================================
   7. PREDICTIONS HUD — 60-min breach queue
   ========================================================= */

function renderPredictions(queue, modelReady = true) {
  const list = document.getElementById('predictions-list');
  if (!list) return;

  if (!modelReady) {
    list.innerHTML = `
      <p class="text-xs text-amber-300/90">Model warming up — accumulating 2 min of telemetry…</p>
      <p class="mt-1 text-[10px] text-slate-500">The forecast engine refuses to guess on thin data.</p>`;
    return;
  }

  if (!queue.length) {
    list.innerHTML = '<p class="text-xs text-slate-500">No breaches projected in the next 60 min ✓</p>';
    return;
  }

  list.innerHTML = queue.slice(0, 4).map(p => {
    const mins = p.minutes_to_capacity;
    const timeLabel = mins != null ? `${mins} min` : '60+';
    const hot = mins != null && mins <= 30;
    return `
      <div class="flex items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5
            ${hot ? 'border-red-500/40 bg-red-500/10' : 'border-amber-500/30 bg-amber-500/5'}">
        <div class="min-w-0">
          <p class="truncate text-[11px] font-semibold text-slate-200">${p.name}</p>
          <p class="font-mono text-[10px] text-slate-500">
            ${Math.round(p.current_pct)}% → ${Math.round(p.projected_pct_60m ?? 0)}%
          </p>
        </div>
        <span class="shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[10px] font-bold
              ${hot ? 'bg-red-500/25 text-red-300' : 'bg-amber-500/20 text-amber-300'}">
          ${timeLabel}
        </span>
      </div>`;
  }).join('');
}

/** REST fallback read — only needed if the WS push has not landed yet. */
function pollPredictionsOnce() {
  fetch(`${CONFIG.API_BASE}/api/predictions`)
    .then(r => (r.ok ? r.json() : Promise.reject()))
    .then(p => renderPredictions(p.breach_queue ?? [], p.model_ready !== false))
    .catch(() => { /* WS will carry it later */ });
}

/* =========================================================
   8. ANOMALY BANNER
   ========================================================= */

let anomalyTimer = null;

function showAnomalyBanner(title, narrative, iconHint) {
  const banner = document.getElementById('anomaly-banner');
  if (!banner) return;

  document.getElementById('anomaly-title').textContent = title;
  document.getElementById('anomaly-narrative').textContent = narrative;

  // Pick an icon from the title's emoji if present, else default.
  const emojiMatch = title.match(/(\p{Emoji_Presentation}|\p{Extended_Pictographic})/u);
  document.getElementById('anomaly-icon').textContent = emojiMatch ? emojiMatch[1] : '⚡';

  banner.classList.remove('anomaly-hidden');
  clearTimeout(anomalyTimer);
  anomalyTimer = setTimeout(hideAnomalyBanner, 12000);
}

function hideAnomalyBanner() {
  document.getElementById('anomaly-banner')?.classList.add('anomaly-hidden');
}

/* =========================================================
   9. TOASTS
   ========================================================= */

function showToast(message, kind = 'error', durationMs = 4200) {
  const root = document.getElementById('toast-root');
  if (root.querySelector(`[data-msg="${CSS.escape(message)}"]`)) return;

  const toast = document.createElement('div');
  toast.className = `dt-toast glass flex items-center gap-2.5 rounded-xl px-4 py-3
                     text-[13px] font-semibold text-slate-100
                     ${kind === 'error' ? 'dt-toast-error' : 'dt-toast-success'}`;
  toast.dataset.msg = message;
  toast.innerHTML = `<span class="text-base leading-none">${kind === 'error' ? '⚠️' : '✅'}</span>
                     <span>${message}</span>`;
  root.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add('dt-toast-in'));
  setTimeout(() => {
    toast.classList.add('dt-toast-out');
    toast.addEventListener('transitionend', () => toast.remove(), { once: true });
    setTimeout(() => toast.remove(), 500);
  }, durationMs);
}

/* =========================================================
   10. UI ACTIONS — collapse, filters, clock, reset
   ========================================================= */

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const chevron = document.getElementById('icon-chevron');
  state.sidebarCollapsed = !state.sidebarCollapsed;

  sidebar.classList.toggle('w-80', !state.sidebarCollapsed);
  sidebar.classList.toggle('w-14', state.sidebarCollapsed);
  sidebar.classList.toggle('sidebar-collapsed', state.sidebarCollapsed);
  chevron.style.transform = state.sidebarCollapsed ? 'rotate(180deg)' : 'rotate(0deg)';

  if (state.map) setTimeout(() => state.map.invalidateSize(), 260);
}

function initFilters() {
  const group = document.getElementById('filter-group');
  group.addEventListener('click', (e) => {
    const btn = e.target.closest('.filter-btn');
    if (!btn) return;
    group.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('is-active'));
    btn.classList.add('is-active');
    state.activeFilter = btn.dataset.filter;

    renderFacilityList();
    for (const [id, marker] of state.markers) {
      const el = marker.getElement();
      if (!el) continue;
      const match = state.activeFilter === 'all' ||
                    state.facilityById.get(id)?.category === state.activeFilter;
      el.style.opacity = match ? '1' : '0.25';
    }
  });
}

function initClock() {
  const el = document.getElementById('live-clock');
  const tick = () => {
    el.textContent = new Date().toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
  };
  tick();
  setInterval(tick, 1000);
}

async function resetDemo() {
  try {
    const res = await fetch(`${CONFIG.API_BASE}/api/reset`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast('Demo baseline restored', 'success');
  } catch {
    showToast('Reset failed — is the backend up?', 'error');
  }
}

/* ── ⚡ Chaos console — on-demand anomaly trigger ───────────── */

/** Populate the dropdown from GET /api/anomalies. */
async function initChaosConsole() {
  const select = document.getElementById('chaos-select');
  if (!select) return;
  try {
    const res = await fetch(`${CONFIG.API_BASE}/api/anomalies`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const { anomalies } = await res.json();
    for (const a of anomalies) {
      const opt = document.createElement('option');
      opt.value = a.id;
      // Keep the emoji, drop the long tail of the title
      opt.textContent = a.title.split('—')[0].trim();
      select.appendChild(opt);
    }
  } catch {
    // Backend not reachable yet — the dropdown keeps its Random option;
    // firing will surface a friendly toast anyway.
  }
}

/** Fire the selected anomaly via POST /api/anomaly/trigger. */
async function fireChaos() {
  const select = document.getElementById('chaos-select');
  const btn = document.getElementById('btn-chaos');
  const anomalyId = select?.value ?? 'random';

  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Firing…';
  try {
    const res = await fetch(`${CONFIG.API_BASE}/api/anomaly/trigger`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ anomaly_id: anomalyId }),
    });
    if (!res.ok) {
      const detail = (await res.json().catch(() => ({})))?.detail ?? `HTTP ${res.status}`;
      throw new Error(detail);
    }
    const { title } = await res.json();
    showToast(`Anomaly incoming: ${title}`, 'success');
  } catch (err) {
    showToast(`Chaos failed: ${err.message ?? err}`, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

/* =========================================================
   BOOTSTRAP
   ========================================================= */
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  initClock();
  initFilters();

  document.getElementById('btn-collapse').addEventListener('click', toggleSidebar);
  document.getElementById('btn-reset').addEventListener('click', resetDemo);
  document.getElementById('btn-chaos').addEventListener('click', fireChaos);
  initChaosConsole();
  document.getElementById('anomaly-close').addEventListener('click', () => {
    clearTimeout(anomalyTimer);
    hideAnomalyBanner();
  });

  // WebSocket-first: snapshot on open, then pure push. One REST warm-up
  // call warms the predictions HUD while the forecast model accumulates.
  pollPredictionsOnce();
  connectWebSocket();

  console.info('[dt] Campus Digital Twin v2 initialized — streaming', CONFIG.WS_URL);
});
