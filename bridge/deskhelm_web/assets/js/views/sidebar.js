// The session list: grouped by project, in the order sessions opened, so a
// row stays where it was while agents work. Each session keeps one row that
// is updated in place, so a streaming reply never replaces the button under
// the pointer.

import { h, icon } from "../dom.js";
import { activityText, activityTone, relativeTime } from "../text.js";

export function avatar(session) {
  const letter = (session.label || session.agent_id || "?").slice(0, 1).toUpperCase();
  return h("span", { class: "avatar", "aria-hidden": "true", dataset: { agent: session.agent_id }, text: letter });
}

function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

export function mountSidebar(store, root, actions) {
  const list = root.querySelector("#session-list");
  const search = root.querySelector("#search");
  const connection = root.querySelector("#connection");
  const theme = root.querySelector("#theme-toggle");
  const rows = new Map();
  let layout = "";
  let frame = 0;

  search.addEventListener("input", () => store.setSearch(search.value.trim().toLowerCase()));
  theme.addEventListener("click", actions.toggleTheme);
  root.querySelector("#close-sidebar").addEventListener("click", () => store.setUi("drawer", false));

  function matches(session) {
    const query = store.state.search;
    if (!query) return true;
    const words = [session.label, session.project_id, session.session_id, session.preview, session.runtime.name];
    return words.join(" ").toLowerCase().includes(query);
  }

  function makeRow(key) {
    const row = {
      avatar: h("span", { class: "avatar", "aria-hidden": "true" }),
      name: h("span", { class: "session-name" }),
      focus: h("span", { class: "focus-mark", title: "语音对这个会话说话" }, icon("mic")),
      unseen: h("span", { class: "unseen", title: "有新的活动" }),
      time: h("span", { class: "session-time" }),
      state: h("span", { class: "state" }),
      preview: h("span", { class: "session-preview" }),
      badge: h("span", { class: "badge" }),
    };
    row.button = h(
      "button",
      { class: "session", type: "button", dataset: { key }, on: { click: () => { store.select(key); store.setUi("drawer", false); } } },
      row.avatar,
      h(
        "span",
        { class: "session-main" },
        h("span", { class: "session-top" }, row.name, row.focus, row.unseen, row.time),
        h("span", { class: "session-sub" }, row.state, row.preview),
      ),
      row.badge,
    );
    rows.set(key, row);
    return row;
  }

  function fill(session) {
    const row = rows.get(session.key) || makeRow(session.key);
    const approvals = session.approvals.filter((ticket) => ticket.kind === "approval").length;
    row.button.className = session.lifecycle === "active" ? "session" : "session is-off";
    if (session.key === store.state.selected) row.button.setAttribute("aria-current", "true");
    else row.button.removeAttribute("aria-current");
    row.avatar.dataset.agent = session.agent_id;
    setText(row.avatar, (session.label || session.agent_id || "?").slice(0, 1).toUpperCase());
    setText(row.name, session.label);
    row.focus.hidden = store.state.focus !== session.key;
    row.unseen.hidden = !store.unseen(session);
    row.time.dataset.time = String(session.updated_at || 0);
    setText(row.time, relativeTime(session.updated_at));
    row.state.className = `state tone-${activityTone(session)}`;
    setText(row.state, activityText(session));
    setText(row.preview, session.preview || "");
    row.preview.hidden = !session.preview;
    row.badge.hidden = !approvals;
    setText(row.badge, String(approvals));
    row.badge.title = `${approvals} 个待批准`;
    return row.button;
  }

  function render() {
    frame = 0;
    const { state } = store;
    const groups = new Map();
    const sessions = [...state.sessions.values()].sort((a, b) => a.opened_at - b.opened_at || a.key.localeCompare(b.key));
    for (const session of sessions) {
      if (!matches(session)) continue;
      if (!groups.has(session.project_id)) groups.set(session.project_id, []);
      groups.get(session.project_id).push(session);
    }
    const ordered = [...groups].sort(([a], [b]) => a.localeCompare(b));
    const signature = JSON.stringify([state.sessions.size, ordered.map(([project, members]) => [project, members.map((session) => session.key)])]);
    if (signature === layout) {
      for (const [, members] of ordered) members.forEach(fill);
    } else {
      layout = signature;
      for (const key of [...rows.keys()]) if (!state.sessions.has(key)) rows.delete(key);
      const nodes = [];
      if (!state.sessions.size) nodes.push(h("p", { class: "list-empty", text: "还没有会话" }));
      else if (!groups.size) nodes.push(h("p", { class: "list-empty", text: "没有匹配的会话" }));
      for (const [project, members] of ordered) {
        nodes.push(h("div", { class: "group" }, h("div", { class: "group-title", text: project }), members.map(fill)));
      }
      list.replaceChildren(...nodes);
    }
    renderConnection();
  }

  function renderConnection() {
    const { state } = store;
    let tone = "ok";
    let text = `已连接 · ${state.sessions.size} 个会话`;
    if (state.stream === "connecting") [tone, text] = ["busy", "正在连接"];
    else if (state.stream === "signed_out") [tone, text] = ["danger", "需要重新登录"];
    else if (state.stream === "lost") [tone, text] = ["danger", "页面已断开，正在重连"];
    else if (!state.connected) [tone, text] = ["warn", "没有连上 Bridge"];
    connection.replaceChildren(h("span", { class: `dot tone-${tone}` }), h("span", { text }));
  }

  const schedule = () => { if (!frame) frame = requestAnimationFrame(render); };
  for (const topic of ["snapshot", "sessions", "selection", "search"]) store.on(topic, schedule);
  // A streamed reply changes one row's preview and time; a search may hide it.
  const changed = new Set();
  let rowFrame = 0;
  store.on("preview", (key) => {
    if (store.state.search) {
      schedule();
      return;
    }
    changed.add(key);
    if (rowFrame) return;
    rowFrame = requestAnimationFrame(() => {
      rowFrame = 0;
      for (const changedKey of changed) {
        const session = store.state.sessions.get(changedKey);
        if (session && rows.has(changedKey)) fill(session);
      }
      changed.clear();
    });
  });
  store.on("connection", renderConnection);
  render();
}
