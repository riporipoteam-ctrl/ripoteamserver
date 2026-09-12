let currentProgress = 0;
const progressBar = document.getElementById('progress-bar');
const loadPercent = document.getElementById('load-percent');
const loadStatus = document.getElementById('load-status');

const statusMessages = [
    "Establishing handshake with Flux RP...",
    "Validating character credentials...",
    "Mounting GTA V high-performance physics cache...",
    "Streaming Los Santos collision models...",
    "Syncing emergency services and police dispatch...",
    "Hydrating banking and vehicle database...",
    "Initializing Flux RP player systems...",
    "Ready for spawn!"
];

let msgIndex = 0;
const statusInterval = setInterval(() => {
    if (msgIndex < statusMessages.length - 1) {
        msgIndex++;
        if (loadStatus) loadStatus.innerText = statusMessages[msgIndex];
    } else {
        clearInterval(statusInterval);
    }
}, 2200);

// FiveM Event Listeners
const handlers = {
    loadProgress(data) {
        currentProgress = Math.min(Math.round(data.loadFraction * 100), 100);
        if (progressBar) progressBar.style.width = currentProgress + '%';
        if (loadPercent) loadPercent.innerText = currentProgress + '%';
    },
    onLogLine(data) {
        if (data.message && loadStatus) {
            loadStatus.innerText = data.message;
        }
    }
};

window.addEventListener('message', function(e) {
    (handlers[e.data.eventName] || function() {})(e.data);
});

// Fallback smooth progress simulation if standalone
let simulated = 0;
const fallbackTimer = setInterval(() => {
    if (currentProgress === 0 && simulated < 95) {
        simulated += Math.floor(Math.random() * 5) + 1;
        if (progressBar) progressBar.style.width = Math.min(simulated, 95) + '%';
        if (loadPercent) loadPercent.innerText = Math.min(simulated, 95) + '%';
    } else {
        clearInterval(fallbackTimer);
    }
}, 400);
