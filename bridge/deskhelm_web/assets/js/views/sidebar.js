// The session list: grouped by project, in the order sessions opened, so a
// row stays where it was while agents work.

import { h, icon } from "../dom.js";
import { activityText, activityTone, relativeTime } from "../text.js";

export function avatar(session) {
  const letter = (session.label || session.agent_id || "?").slice(0, 1).toUpperCase();
  return h("span", { class: "avatar", "aria-hidden": "true", dataset: { agent: session.agent_id }, text: letter });
}

export function mountSidebar(store, root, actions) {
  const list = root.querySelector("#session-list");
  const search = root.querySelector("#search");
  const connection = root.querySelector("#connection");
  const theme = root.querySelector("#theme-toggle");
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

  function row(session) {
    const approvals = session.approvals.filter((ticket) => ticket.kind === "approval").length;
    const focused = store.state.focus === session.key;
    return h(
      "button",
      {
        class: session.lifecycle === "active" ? "session" : "session is-off",
        type: "button",
        "aria-current": session.key === store.state.selected ? "true" : null,
        dataset: { key: session.key },
        on: { click: () => { store.select(session.key); store.setUi("drawer", false); } },
      },
      avatar(session),
      h(
        "span",
        { class: "session-main" },
        h(
          "span",
          { class: "session-top" },
          h("span", { class: "session-name", text: session.label }),
          focused ? h("span", { class: "focus-mark", title: "语音对这个会话说话" }, icon("mic")) : null,
          h("span", { class: "session-time", dataset: { time: String(session.updated_at || 0) }, text: relativeTime(session.updated_at) }),
        ),
        h(
          "span",
          { class: "session-sub" },
          h("span", { class: `state tone-${activityTone(session)}`, text: activityText(session) }),
          session.preview ? h("span", { class: "session-preview", text: session.preview }) : null,
        ),
      ),
      approvals ? h("span", { class: "badge", title: `${approvals} 个待批准`, text: String(approvals) }) : null,
    );
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
    const nodes = [];
    if (!state.sessions.size) nodes.push(h("p", { class: "list-empty", text: "还没有会话" }));
    else if (!groups.size) nodes.push(h("p", { class: "list-empty", text: "没有匹配的会话" }));
    for (const [project, members] of [...groups].sort(([a], [b]) => a.localeCompare(b))) {
      nodes.push(h("div", { class: "group" }, h("div", { class: "group-title", text: project }), members.map(row)));
    }
    list.replaceChildren(...nodes);
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
  for (const topic of ["snapshot", "sessions", "selection", "search", "preview"]) store.on(topic, schedule);
  store.on("connection", renderConnection);
  render();
}
