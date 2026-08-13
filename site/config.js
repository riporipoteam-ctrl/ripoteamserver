window.RIPO_CONFIG = {
  spaceUrl: "https://echoxr-ripoteam-cloud-pc.hf.space",
  cloudflareProxyUrl: "",
  pollSeconds: 5,
  wakeTimeoutSeconds: 300,
  deploymentVersion: "2026-08-13-server-tiktok-connect-1"
};

(() => {
  const script = document.createElement('script');
  script.src = './tiktok-server-connect.js?v=20260813-1';
  script.defer = true;
  document.head.appendChild(script);
})();
