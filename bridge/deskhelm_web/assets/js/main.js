// Wires the store, the event stream, the views, and the page's actions.

import { catalog, control, openStream, sessionFields } from "./api.js";
import { createStore, storageGet, storageSet } from "./store.js";
import { countdown, relativeTime, resultText } from "./text.js";
import { mountComposer } from "./views/composer.js";
import { mountDetails } from "./views/details.js";
import { mountHeader } from "./views/header.js";
import { mountSidebar } from "./views/sidebar.js";
import { mountTranscript } from "./views/transcript.js";

const THEME_KEY = "deskhelm-web-theme";
const store = createStore();
const app = document.getElementById("app");
const live = document.getElementById("live");
let composer = null;

function announce(text) {
  live.textContent = "";
  requestAnimationFrame(() => {
    live.textContent = text;
  });
}

async function run(kind, session, extra) {
  const result = await control({ kind, ...sessionFields(session), ...extra });
  announce(resultText(result.payload, result.ok));
  return result;
}

const actions = {
  announce,
  async focus() {
    const session = store.selected();
    if (session) await run("focus", session);
  },
  async interrupt() {
    const session = store.selected();
    if (session) await run("interrupt", session);
  },
  async decide(kind, approvalId) {
    const session = store.selected();
    if (!session || store.state.deciding.has(approvalId)) return;
    store.setDeciding(approvalId, true);
    const result = await run(kind, session, { approval_id: approvalId });
    store.setDeciding(approvalId, false);
    if (result.ok && result.payload.code === "dispatched") store.setDecided(approvalId, kind);
  },
  async loadCatalog(session) {
    store.setCatalog(session.key, { status: "loading", data: null });
    const result = await catalog(session);
    store.setCatalog(session.key, { status: result.ok ? "ready" : "failed", data: result.payload });
  },
  async configure(session, model, effort) {
    const entry = store.state.catalogs.get(session.key);
    if (!entry || entry.status !== "ready" || entry.applying) return;
    store.setCatalog(session.key, { ...entry, applying: true });
    const result = await run("configure_session", session, { model, effort: effort || null });
    const data = { ...entry.data };
    if (result.ok && result.payload.code === "dispatched") {
      data.model = model;
      data.reasoning_effort = effort;
    }
    store.setCatalog(session.key, { status: "ready", data });
  },
  insertText(text) {
    if (composer) composer.insert(text);
  },
  toggleTheme() {
    applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light", true);
  },
};

function applyTheme(theme, remember) {
  document.documentElement.dataset.theme = theme;
  const button = document.getElementById("theme-toggle");
  button.setAttribute("aria-label", theme === "light" ? "切换到深色" : "切换到浅色");
  button.title = button.getAttribute("aria-label");
  if (remember) storageSet(THEME_KEY, theme);
}

function syncLayout() {
  const { ui } = store.state;
  app.classList.toggle("show-details", ui.details && Boolean(store.selected()));
  app.classList.toggle("show-drawer", ui.drawer);
  document.getElementById("scrim").hidden = !(ui.drawer || ui.details);
}

const stored = storageGet(THEME_KEY);
applyTheme(stored || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"), false);

mountSidebar(store, document.getElementById("sidebar"), actions);
mountHeader(store, document.getElementById("topbar"), document.getElementById("banner"), actions);
mountTranscript(store, {
  scroller: document.getElementById("scroller"),
  root: document.getElementById("transcript"),
  jump: document.getElementById("jump"),
}, actions);
composer = mountComposer(store, document.getElementById("composer"), actions);
mountDetails(store, document.getElementById("details"), actions);

for (const topic of ["ui", "selection", "snapshot"]) store.on(topic, syncLayout);

// The tab title counts the approvals waiting anywhere, for a page in the background.
function syncTitle() {
  let waiting = 0;
  for (const session of store.state.sessions.values()) {
    waiting += session.approvals.filter((ticket) => ticket.kind === "approval").length;
  }
  document.title = waiting ? `(${waiting}) DeskHelm` : "DeskHelm";
}
for (const topic of ["snapshot", "sessions"]) store.on(topic, syncTitle);
document.getElementById("scrim").addEventListener("click", () => {
  store.setUi("drawer", false);
  store.setUi("details", false);
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const name of ["menu", "drawer", "details"]) {
    if (store.state.ui[name]) {
      store.setUi(name, false);
      return;
    }
  }
});

// Countdowns every second; relative times every half minute.
let ticks = 0;
setInterval(() => {
  const now = Date.now();
  for (const node of document.querySelectorAll("[data-expires]")) node.textContent = countdown(Number(node.dataset.expires), now);
  ticks += 1;
  if (ticks % 30 === 0) {
    for (const node of document.querySelectorAll("[data-time]")) node.textContent = relativeTime(Number(node.dataset.time), now);
  }
}, 1000);

syncLayout();
openStream(store);
