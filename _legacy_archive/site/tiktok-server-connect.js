(() => {
  const d = window.RIPO_CONFIG || {};
  const base = () => String(localStorage.getItem('ripo-proxy-url') || localStorage.getItem('ripo-space-url') || d.cloudflareProxyUrl || d.spaceUrl || '').replace(/\/+$/, '');

  function install() {
    const button = document.querySelector('#oauth');
    const account = document.querySelector('#account');
    if (!button || button.dataset.serverConnectInstalled) return;
    button.dataset.serverConnectInstalled = '1';
    button.textContent = 'Connect TikTok to Server';

    button.onclick = async () => {
      let popup = null;
      try {
        popup = window.open('about:blank', 'ripoTikTokServer', 'width=520,height=760');
        if (account) account.textContent = 'Opening TikTok on the Ripo server computer…';
        const r = await fetch(base() + '/api/tiktok/server-connect/start', {
          method: 'POST',
          cache: 'no-store',
          headers: {'content-type': 'application/json'},
          body: '{}'
        });
        const x = await r.json();
        if (!r.ok) throw new Error(x.detail || x.message || `HTTP ${r.status}`);
        const target = './tiktok-server-connect.html#' + new URLSearchParams({
          server: base(),
          token: x.desktop_token,
          flow: x.flow_id
        }).toString();
        if (popup) popup.location.replace(target);
        else location.href = target;
        if (account) account.textContent = 'TikTok opened on the server computer. Finish TikTok login there.';
      } catch (e) {
        try { popup?.close(); } catch {}
        if (account) account.textContent = e.message || String(e);
      }
    };

    window.addEventListener('message', (event) => {
      if (event.origin !== location.origin || event.data?.type !== 'ripo-server-tiktok-connected') return;
      if (event.data.session_token) {
        localStorage.setItem('ripo-tiktok-session', event.data.session_token);
        if (account) account.textContent = 'TikTok connected to the Ripo server computer ✅';
        setTimeout(() => document.querySelector('#refresh')?.click(), 150);
      }
    });
  }

  if (document.readyState === 'complete') install();
  else window.addEventListener('load', install, {once:true});
})();
