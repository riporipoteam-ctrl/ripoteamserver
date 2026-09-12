(() => {
  const $ = (s) => document.querySelector(s);
  const d = window.RIPO_CONFIG || {};
  const base = () => String(localStorage.getItem('ripo-proxy-url') || localStorage.getItem('ripo-space-url') || d.cloudflareProxyUrl || d.spaceUrl || '').replace(/\/+$/, '');
  const token = () => localStorage.getItem('ripo-tiktok-session') || localStorage.getItem('ripo-admin-token') || '';
  const headers = () => token() ? {'x-admin-token': token()} : {};

  function phaseLabel(x) {
    const p = String(x.phase || '').toLowerCase();
    if (x.connected) return 'Connected to LIVE';
    if (p === 'waiting-for-live') return 'Waiting for you to go LIVE';
    if (p === 'checking-live') return 'Checking TikTok LIVE';
    if (p === 'connecting') return 'LIVE found — connecting';
    if (p === 'retrying') return 'Connection problem — retrying';
    if (p === 'reconnecting') return 'LIVE ended/dropped — watching again';
    if (p === 'finished') return 'Session finished';
    if (p === 'error') return 'Worker error';
    if (x.running) return 'AI watcher running';
    return 'Stopped';
  }

  function addUI() {
    const start = $('#start');
    if (start) start.textContent = 'Start / Watch LIVE';
    const stop = $('#stop');
    if (stop) stop.textContent = 'Stop AI watcher';

    const runCard = start?.closest('.card');
    if (runCard && !$('#watcher-detail')) {
      const p = document.createElement('div');
      p.id = 'watcher-detail';
      p.className = 'notice';
      p.style.marginTop = '12px';
      p.innerHTML = '<b>Background watcher:</b> Start once and the server will keep checking for your LIVE, connect automatically, and reconnect after drops. You do <b>not</b> have to keep this dashboard open for event monitoring.';
      runCard.appendChild(p);
    }

    const statusRow = document.querySelector('.status');
    if (statusRow && !$('#watcher-pill')) {
      const pill = document.createElement('span');
      pill.id = 'watcher-pill';
      pill.className = 'pill';
      pill.textContent = 'Watcher: checking';
      statusRow.appendChild(pill);
    }

    const grid = document.querySelector('.grid');
    if (grid && !$('#diagnostics-card')) {
      const card = document.createElement('article');
      card.id = 'diagnostics-card';
      card.className = 'card wide';
      card.innerHTML = `
        <h2>AI watcher diagnostics</h2>
        <div class="metrics">
          <div class="metric"><small>Phase</small><strong id="diag-phase">—</strong></div>
          <div class="metric"><small>Retries</small><strong id="diag-retries">0</strong></div>
          <div class="metric"><small>Next check</small><strong id="diag-next">—</strong></div>
          <div class="metric"><small>Background</small><strong id="diag-bg">Ready</strong></div>
        </div>
        <p id="diag-error">No connection errors.</p>
        <div class="notice"><b>Important:</b> “Start / Watch LIVE” starts the AI watcher, not a TikTok broadcast. Start your actual LIVE in TikTok; the AI will detect it automatically. Server event monitoring can continue while you are away. Audible AI speech is still delivered through this dashboard’s browser audio, so closing every playback device means viewers cannot hear the generated voice.</div>
      `;
      const activity = [...grid.querySelectorAll('.card')].find(el => el.querySelector('h2')?.textContent.includes('LIVE activity'));
      if (activity) grid.insertBefore(card, activity);
      else grid.appendChild(card);
    }
  }

  async function poll() {
    try {
      if (!base()) return;
      const r = await fetch(base() + '/api/tiktok/status', {cache:'no-store', headers: headers()});
      const x = await r.json();
      if (!r.ok) throw new Error(x.detail || x.message || `HTTP ${r.status}`);
      const label = phaseLabel(x);
      const live = $('#live');
      if (live) {
        live.textContent = 'AI: ' + label;
        live.className = x.connected || x.running ? 'pill on' : 'pill';
      }
      const state = $('#state');
      if (state) state.textContent = label;
      const wp = $('#watcher-pill');
      if (wp) {
        wp.textContent = x.auto_reconnect ? 'Watcher: auto-reconnect ON' : 'Watcher: standard';
        wp.className = x.running ? 'pill on' : 'pill';
      }
      const phase = $('#diag-phase');
      if (phase) phase.textContent = label;
      const retries = $('#diag-retries');
      if (retries) retries.textContent = String(x.retry_count || 0);
      const next = $('#diag-next');
      if (next) next.textContent = x.next_retry_seconds == null ? '—' : `${x.next_retry_seconds}s`;
      const bg = $('#diag-bg');
      if (bg) bg.textContent = x.background_worker ? 'Server-side' : 'Browser-only';
      const err = $('#diag-error');
      if (err) {
        err.textContent = x.last_error ? `Last error: ${x.last_error}` : (x.running && !x.connected ? 'No fatal error — watcher is active.' : 'No connection errors.');
        err.style.color = x.last_error ? '#ff91a7' : '';
      }
    } catch (e) {
      const err = $('#diag-error');
      if (err) err.textContent = 'Dashboard cannot reach the server right now: ' + (e.message || e);
    } finally {
      setTimeout(poll, 2200);
    }
  }

  function init() {
    addUI();
    poll();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
})();
