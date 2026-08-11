"use strict";

const defaults = window.RIPO_CONFIG || {};
const DEPLOYMENT_VERSION = "2026-08-11-local-ai-v1";
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
  overviewDot: q("#overview-dot"), overviewState: q("#overview-state"), overviewArch: q("#overview-arch"), overviewMemory: q("#overview-memory"), overviewAi: q("#overview-ai"),
  clock: q("#clock"), subtitle: q("#desktop-subtitle"), panel: q("#connect-panel"), title: q("#connect-title"),
  message: q("#connect-message"), progress: q("#progress"), frame: q("#desktop-frame"), health: q("#health-json"),
  result: q("#hermes-result"), log: q("#log-output"), spaceInput: q("#space-url"), proxyInput: q("#proxy-url"),
  tokenInput: q("#admin-token"), installApp: q("#install-app"), aiDot: q("#ai-dot"), aiStage: q("#ai-stage"), aiMessage: q("#ai-message"),
  aiModel: q("#ai-model"), aiModelDetail: q("#ai-model-detail"), aiOllama: q("#ai-ollama"), aiHermes: q("#ai-hermes"),
  aiHermesDetail: q("#ai-hermes-detail"), aiTelegram: q("#ai-telegram"), aiTelegramDetail: q("#ai-telegram-detail"),
  aiSkills: q("#ai-skills"), aiPlugins: q("#ai-plugins"), pairCode: q("#telegram-pair-code"), secretWarning: q("#telegram-secret-warning"),
};

let connecting = false;
let lastHealth = null;
let healthTimer = 0;
let installPrompt = null;
let aiBusy = false;

function base() { return (cfg.proxyUrl || cfg.spaceUrl).replace(/\/+$/, ""); }
function direct() { return cfg.spaceUrl.replace(/\/+$/, ""); }
function desktopUrl() { return `${direct()}/desktop?v=${encodeURIComponent(DEPLOYMENT_VERSION)}`; }
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

function setState(state, text) {
  for (const dot of [el.statusDot, el.overviewDot]) if (dot) dot.className = `dot ${state}`;
  if (el.statusText) el.statusText.textContent = text;
  if (el.overviewState) el.overviewState.textContent = state === "online" ? "Online" : state === "offline" ? "Unavailable" : "Starting";
  const metric = q("#metric-state");
  if (metric) metric.textContent = state === "online" ? "Online" : state === "offline" ? "Offline" : "Starting";
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
  if (el.clock) el.clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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

function renderAi(data) {
  if (!data) return;
  const model = data.model || {};
  const ollama = data.ollama || {};
  const hermes = data.hermes || {};
  const telegram = data.telegram || {};
  const bootstrap = data.bootstrap || {};

  if (el.aiModel) el.aiModel.textContent = model.name || "qwen3:4b";
  if (el.aiModelDetail) el.aiModelDetail.textContent = model.installed ? `Installed · ${Math.round((model.context_length || 0) / 1024)}K context` : "Downloading / not installed yet";
  if (el.aiOllama) el.aiOllama.textContent = ollama.running ? "Running" : ollama.installed ? "Installed" : "Not installed";
  if (el.aiHermes) el.aiHermes.textContent = hermes.gateway_running ? "Gateway running" : hermes.installed ? "Installed" : "Not installed";
  if (el.aiHermesDetail) el.aiHermesDetail.textContent = hermes.gateway_running ? "Local model + Telegram active" : "Agent + tool gateway";
  if (el.aiTelegram) el.aiTelegram.textContent = telegram.token_configured ? (hermes.gateway_running ? "Connected" : "Configured") : "Secret missing";
  if (el.aiTelegramDetail) el.aiTelegramDetail.textContent = telegram.access_mode === "allowlist" ? "Restricted allowlist" : "Default deny · pairing";
  if (el.aiSkills) el.aiSkills.textContent = Number.isFinite(hermes.skills) ? `${hermes.skills}` : "—";
  if (el.aiPlugins) el.aiPlugins.textContent = Number.isFinite(hermes.plugins) ? `${hermes.plugins}` : "—";
  if (el.secretWarning) el.secretWarning.classList.toggle("hidden", Boolean(telegram.token_configured));

  const ready = ollama.running && model.installed && hermes.installed;
  const state = bootstrap.stage === "error" ? "offline" : ready ? "online" : "checking";
  if (el.aiDot) el.aiDot.className = `dot ${state}`;
  if (el.aiStage) el.aiStage.textContent = bootstrap.running ? "Preparing local AI…" : ready ? "Local AI ready" : bootstrap.stage === "error" ? "Setup needs attention" : "Preparing…";
  if (el.aiMessage) el.aiMessage.textContent = bootstrap.last_error || bootstrap.message || "Qwen and Hermes prepare automatically.";
  if (el.overviewAi) el.overviewAi.textContent = model.installed && ollama.running ? (hermes.gateway_running ? "Qwen + Hermes online" : "Qwen online") : bootstrap.running ? "Installing…" : "Preparing…";
  const metricHermes = q("#metric-hermes");
  if (metricHermes) metricHermes.textContent = hermes.gateway_running ? "Running" : hermes.installed ? "Installed" : "Not installed";
}

function renderHealth(data) {
  lastHealth = data;
  if (el.health) el.health.textContent = JSON.stringify(data, null, 2);
  const architecture = data.architecture || "—";
  const memory = data.memory_total ? `${bytes(data.memory_available)} free / ${bytes(data.memory_total)}` : "Limit unavailable";
  q("#metric-arch").textContent = architecture;
  q("#metric-cpu").textContent = data.cpu_count ? `${data.cpu_count} vCPU` : "—";
  q("#metric-memory").textContent = memory;
  q("#metric-disk").textContent = data.disk_total ? `${bytes(data.disk_free)} free / ${bytes(data.disk_total)}` : (data.disk_note || "Ephemeral storage");
  if (el.overviewArch) el.overviewArch.textContent = architecture;
  if (el.overviewMemory) el.overviewMemory.textContent = data.memory_total ? bytes(data.memory_total) : "Managed by host";
  if (data.ai) renderAi(data.ai);
}

async function health() {
  const data = await request("/api/health", {}, 20000);
  renderHealth(data);
  return data;
}

async function aiStatus() {
  const data = await request("/api/ai/status", {}, 12000);
  renderAi(data);
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
    el.message.textContent = `Wake attempt ${attempt}. Desktop can appear before the local model finishes loading.`;
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

async function aiAction(path, label, timeoutMs = 45000, body = null) {
  if (aiBusy) return;
  aiBusy = true;
  if (el.result) el.result.textContent = `${label}…`;
  try {
    const options = { method: "POST" };
    if (body !== null) {
      options.headers = { "content-type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const data = await request(path, options, timeoutMs);
    if (el.result) el.result.textContent = data.message || JSON.stringify(data);
    await sleep(700);
    await aiStatus().catch(() => {});
    await health().catch(() => {});
  } catch (error) {
    if (el.result) el.result.textContent = error.name === "AbortError" ? `${label} is still running. Refresh status in a moment.` : error.message;
  } finally {
    aiBusy = false;
  }
}

async function loadLog(name) {
  el.log.textContent = `Loading ${name}…`;
  try { const data = await request(`/api/logs/${name}`); el.log.textContent = data.content; }
  catch (error) { el.log.textContent = error.message; }
}

async function loadAiLog(name) {
  el.log.textContent = `Loading AI log ${name}…`;
  try { const data = await request(`/api/ai/logs/${name}`); el.log.textContent = data.content; }
  catch (error) { el.log.textContent = error.message; }
}

function openWindow(id, sourceButton) {
  const panel = document.getElementById(id);
  if (!panel) return;
  panel.classList.remove("hidden");
  qa(".dock button").forEach((button) => button.classList.toggle("active", button === sourceButton));
  if (id === "hermes") aiStatus().catch((error) => { if (el.result) el.result.textContent = error.message; });
}

qa("[data-window]").forEach((button) => button.addEventListener("click", () => openWindow(button.dataset.window, button)));
qa("[data-close]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.close)?.classList.add("hidden")));
qa("[data-log]").forEach((button) => button.addEventListener("click", () => loadLog(button.dataset.log)));
qa("[data-ai-log]").forEach((button) => button.addEventListener("click", () => loadAiLog(button.dataset.aiLog)));
q("#connect-button").onclick = connect;
q("#reconnect").onclick = () => { el.frame.src = "about:blank"; setTimeout(connect, 80); };
q("#fullscreen").onclick = async () => {
  try {
    if (!document.fullscreenElement) await q("#desktop").requestFullscreen();
    else await document.exitFullscreen();
  } catch { window.open(desktopUrl(), "_blank", "noopener,noreferrer"); }
};
q("#setup-ai").onclick = () => aiAction("/api/ai/bootstrap", "Starting AI setup");
q("#start-ai").onclick = () => aiAction("/api/ai/ollama/start", "Starting Ollama", 90000);
q("#stop-ai").onclick = () => aiAction("/api/ai/ollama/stop", "Stopping Ollama");
q("#pull-model").onclick = () => aiAction("/api/ai/model/pull", "Pulling Qwen3 4B", 600000);
q("#start-hermes").onclick = () => aiAction("/api/ai/hermes/start", "Starting Hermes Telegram gateway", 90000);
q("#stop-hermes").onclick = () => aiAction("/api/ai/hermes/stop", "Stopping Hermes gateway");
q("#refresh-hermes").onclick = () => aiStatus().catch((error) => { if (el.result) el.result.textContent = error.message; });
q("#approve-pair").onclick = () => {
  const code = (el.pairCode?.value || "").trim();
  if (!code) { el.result.textContent = "Enter the pairing code Hermes sent you on Telegram."; return; }
  aiAction("/api/ai/telegram/pair", "Approving Telegram pairing", 60000, { code });
};
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
