"use strict";

const defaults = window.RIPO_CONFIG || {};
const DEPLOYMENT_VERSION = "2026-08-03-mobile-control-v3";
const cfg = {
  spaceUrl: localStorage.getItem("ripo-space-url") || defaults.spaceUrl || "",
  proxyUrl: localStorage.getItem("ripo-proxy-url") || defaults.cloudflareProxyUrl || "",
  adminToken: localStorage.getItem("ripo-admin-token") || "",
  pollSeconds: Number(defaults.pollSeconds || 5),
  wakeTimeoutSeconds: Number(defaults.wakeTimeoutSeconds || 300),
};

const q = (selector) => document.querySelector(selector);
const qa = (selector) => [...document.querySelectorAll(selector)];
const el = {
  boot: q("#boot"), app: q("#app"), statusDot: q("#status-dot"), statusText: q("#status-text"),
  overviewDot: q("#overview-dot"), overviewState: q("#overview-state"), overviewArch: q("#overview-arch"), overviewMemory: q("#overview-memory"),
  clock: q("#clock"), subtitle: q("#desktop-subtitle"), panel: q("#connect-panel"), title: q("#connect-title"),
  message: q("#connect-message"), progress: q("#progress"), frame: q("#desktop-frame"), health: q("#health-json"),
  result: q("#hermes-result"), log: q("#log-output"), spaceInput: q("#space-url"), proxyInput: q("#proxy-url"),
  tokenInput: q("#admin-token"), installApp: q("#install-app"),
};

let connecting = false;
let lastHealth = null;
let healthTimer = 0;
let installPrompt = null;

function base() { return (cfg.proxyUrl || cfg.spaceUrl).replace(/\/+$/, ""); }
function direct() { return cfg.spaceUrl.replace(/\/+$/, ""); }
function desktopUrl() { return `${direct()}/desktop?v=${encodeURIComponent(DEPLOYMENT_VERSION)}`; }
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function setState(state, text) {
  for (const dot of [el.statusDot, el.overviewDot]) dot.className = `dot ${state}`;
  el.statusText.textContent = text;
  el.overviewState.textContent = state === "online" ? "Online" : state === "offline" ? "Unavailable" : "Starting";
  q("#metric-state").textContent = state === "online" ? "Online" : state === "offline" ? "Offline" : "Starting";
}

function bytes(value) {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let number = value;
  let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return `${number.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function updateClock() {
  el.clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function request(path, options = {}, timeoutMs = 25000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const headers = { ...(options.headers || {}) };
  if (cfg.adminToken) headers["x-admin-token"] = cfg.adminToken;
  try {
    const response = await fetch(`${base()}${path}`, { cache: "no-store", ...options, headers, signal: controller.signal });
    let data;
    try { data = await response.json(); } catch { data = { ok: false, message: `HTTP ${response.status}` }; }
    if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

function renderHealth(data) {
  lastHealth = data;
  el.health.textContent = JSON.stringify(data, null, 2);
  const architecture = data.architecture || "—";
  const memory = data.memory_total ? `${bytes(data.memory_available)} free / ${bytes(data.memory_total)}` : "Limit unavailable";
  q("#metric-arch").textContent = architecture;
  q("#metric-cpu").textContent = data.cpu_count ? `${data.cpu_count} vCPU` : "—";
  q("#metric-memory").textContent = memory;
  q("#metric-disk").textContent = data.disk_total ? `${bytes(data.disk_free)} free / ${bytes(data.disk_total)}` : (data.disk_note || "Ephemeral storage");
  q("#metric-hermes").textContent = data.hermes?.running ? "Running" : data.hermes?.installed ? "Installed" : "Not installed";
  el.overviewArch.textContent = architecture;
  el.overviewMemory.textContent = data.memory_total ? bytes(data.memory_total) : "Managed by host";
}

async function health() {
  const data = await request("/api/health", {}, 20000);
  renderHealth(data);
  return data;
}

function setDesktopLinks() {
  const open = () => {
    if (!direct()) return;
    const opened = window.open(desktopUrl(), "_blank", "noopener,noreferrer");
    if (!opened) window.location.href = desktopUrl();
  };
  for (const id of ["#open-desktop", "#open-desktop-top", "#open-direct-wait", "#overview-open"]) {
    const button = q(id);
    if (button) button.onclick = open;
  }
}

async function connect() {
  if (connecting || !base()) return;
  connecting = true;
  clearTimeout(healthTimer);
  el.frame.classList.add("hidden");
  el.panel.classList.remove("hidden");
  setState("checking", "Waking server");
  el.subtitle.textContent = "Waking…";
  el.title.textContent = "Waking your cloud computer";
  const deadline = Date.now() + cfg.wakeTimeoutSeconds * 1000;
  let attempt = 0;

  while (Date.now() < deadline) {
    attempt += 1;
    const used = 1 - (deadline - Date.now()) / (cfg.wakeTimeoutSeconds * 1000);
    el.progress.style.width = `${Math.min(94, 6 + used * 87)}%`;
    el.message.textContent = `Wake attempt ${attempt}. The first rebuild can take longer than a normal wake.`;
    try {
      await health();
      setState("online", "Server online");
      el.subtitle.textContent = "Connected";
      el.progress.style.width = "100%";
      el.title.textContent = "Your Linux computer is ready";
      el.message.textContent = "Opening the desktop…";
      await sleep(350);
      el.frame.src = desktopUrl();
      el.panel.classList.add("hidden");
      el.frame.classList.remove("hidden");
      connecting = false;
      scheduleHealth();
      return;
    } catch {
      const delay = Math.min(12000, cfg.pollSeconds * 1000 + attempt * 450);
      await sleep(delay);
    }
  }

  setState("offline", "Unavailable");
  el.subtitle.textContent = "Connection failed";
  el.title.textContent = "The Space did not become ready";
  el.message.textContent = "Open the full-page computer or check the Hugging Face Space status.";
  el.progress.style.width = "0%";
  connecting = false;
}

function scheduleHealth() {
  clearTimeout(healthTimer);
  if (document.hidden) return;
  healthTimer = setTimeout(async () => {
    try { await health(); setState("online", "Server online"); }
    catch { setState("checking", "Reconnecting"); }
    scheduleHealth();
  }, 35000);
}

async function hermesAction(action) {
  el.result.textContent = `Running ${action}…`;
  try {
    const data = await request(`/api/hermes/${action}`, { method: "POST" }, 40000);
    el.result.textContent = data.message || JSON.stringify(data);
    setTimeout(() => health().catch(() => {}), 1500);
  } catch (error) { el.result.textContent = error.message; }
}

async function loadLog(name) {
  el.log.textContent = `Loading ${name}…`;
  try { const data = await request(`/api/logs/${name}`); el.log.textContent = data.content; }
  catch (error) { el.log.textContent = error.message; }
}

function openWindow(id, sourceButton) {
  const panel = document.getElementById(id);
  if (!panel) return;
  panel.classList.remove("hidden");
  qa(".dock button").forEach((button) => button.classList.toggle("active", button === sourceButton));
}

qa("[data-window]").forEach((button) => button.addEventListener("click", () => openWindow(button.dataset.window, button)));
qa("[data-close]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.close)?.classList.add("hidden")));
qa("[data-log]").forEach((button) => button.addEventListener("click", () => loadLog(button.dataset.log)));
q("#connect-button").onclick = connect;
q("#reconnect").onclick = () => { el.frame.src = "about:blank"; setTimeout(connect, 80); };
q("#fullscreen").onclick = async () => {
  try {
    if (!document.fullscreenElement) await q("#desktop").requestFullscreen();
    else await document.exitFullscreen();
  } catch { window.open(desktopUrl(), "_blank", "noopener,noreferrer"); }
};
q("#install-hermes").onclick = () => hermesAction("install");
q("#start-hermes").onclick = () => hermesAction("start");
q("#stop-hermes").onclick = () => hermesAction("stop");
q("#refresh-hermes").onclick = () => health().catch((error) => { el.result.textContent = error.message; });
q("#save-settings").onclick = () => {
  cfg.spaceUrl = el.spaceInput.value.trim();
  cfg.proxyUrl = el.proxyInput.value.trim();
  cfg.adminToken = el.tokenInput.value;
  localStorage.setItem("ripo-space-url", cfg.spaceUrl);
  localStorage.setItem("ripo-proxy-url", cfg.proxyUrl);
  localStorage.setItem("ripo-admin-token", cfg.adminToken);
  setDesktopLinks();
  connect();
};

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  el.installApp.classList.remove("hidden");
});
el.installApp.onclick = async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  el.installApp.classList.add("hidden");
};

document.addEventListener("visibilitychange", () => {
  if (document.hidden) clearTimeout(healthTimer);
  else { health().then(() => setState("online", "Server online")).catch(() => setState("checking", "Reconnecting")); scheduleHealth(); }
});
window.addEventListener("online", () => connect());
window.addEventListener("offline", () => setState("offline", "Device offline"));

async function boot() {
  el.spaceInput.value = cfg.spaceUrl;
  el.proxyInput.value = cfg.proxyUrl;
  el.tokenInput.value = cfg.adminToken;
  setDesktopLinks();
  updateClock();
  setInterval(updateClock, 1000);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("./sw.js").catch(() => {});
  await sleep(520);
  el.boot.classList.add("hidden");
  el.app.classList.remove("hidden");
  connect();
}

boot();
