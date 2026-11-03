// The top bar of the selected session, and the banner for connection trouble.

import { h, icon } from "../dom.js";
import { NOTICE_TEXT, activityText, activityTone } from "../text.js";
import { avatar } from "./sidebar.js";

export function runtimeLine(session) {
  const { name, version } = session.runtime;
  if (!version) return name;
  return name && !version.toLowerCase().includes(name.toLowerCase()) ? `${name} ${version}` : version;
}

export function mountHeader(store, root, banner, actions) {
  const badge = h("span", { class: "title-avatar" });
  const title = h("h1", { text: "DeskHelm" });
  const pill = h("span", { class: "pill", hidden: true });
  const subtitle = h("p", { class: "subtitle" });
  const focusLabel = h("span", { class: "label", text: "语音聚焦" });
  const focus = h("button", { class: "button ghost", type: "button", on: { click: actions.focus } }, icon("mic"), focusLabel);
  const details = h(
    "button",
    { class: "icon-button", type: "button", title: "会话详情", "aria-label": "会话详情", on: { click: () => store.setUi("details") } },
    icon("info"),
  );
  root.replaceChildren(
    h("button", { class: "icon-button only-narrow", type: "button", "aria-label": "会话列表", on: { click: () => store.setUi("drawer", true) } }, icon("menu")),
    badge,
    h("div", { class: "title" }, h("div", { class: "title-line" }, title, pill), subtitle),
    h("div", { class: "actions" }, focus, details),
  );

  function render() {
    const session = store.selected();
    const { state } = store;
    badge.hidden = !session;
    badge.replaceChildren(session ? avatar(session) : "");
    title.textContent = session ? session.label : "DeskHelm";
    subtitle.textContent = session ? [session.project_id, runtimeLine(session)].filter(Boolean).join(" · ") : "本地 Agent 控制台";
    pill.hidden = !session;
    if (session) {
      pill.className = `pill tone-${activityTone(session)}`;
      pill.replaceChildren(h("span", { class: "dot" }), activityText(session));
    }
    const focused = Boolean(session) && state.focus === session.key;
    focus.hidden = !session;
    focus.disabled = !session || focused || session.lifecycle !== "active" || !state.connected;
    focus.classList.toggle("is-on", focused);
    focusLabel.textContent = focused ? "语音已聚焦" : "语音聚焦";
    focus.title = focused ? "按住右 Alt 说话会发给这个会话" : "让按住右 Alt 说的话发给这个会话";
    details.hidden = !session;
    details.setAttribute("aria-expanded", state.ui.details ? "true" : "false");
    renderBanner();
  }

  // An informational notice stays dismissed for the Bridge instance it was about.
  let dismissed = "";

  function renderBanner() {
    const { state } = store;
    const notice = `${state.epoch}:${state.notice}`;
    let text = "";
    let tone = "warn";
    if (state.stream === "signed_out") [text, tone] = ["登录已失效。请重新打开 deskhelm web 启动时打印的链接。", "danger"];
    else if (state.stream === "lost") text = "与页面服务的连接已断开，正在重连…";
    else if (state.stream === "open" && !state.connected) text = NOTICE_TEXT.bridge_unavailable;
    else if (NOTICE_TEXT[state.notice] && notice !== dismissed) [text, tone] = [NOTICE_TEXT[state.notice], "info"];
    banner.hidden = !text;
    banner.className = `banner tone-${tone}`;
    const close = tone === "info"
      ? h("button", { class: "icon-button", type: "button", "aria-label": "知道了", on: { click: () => { dismissed = notice; renderBanner(); } } }, icon("close"))
      : null;
    banner.replaceChildren(icon(tone === "info" ? "info" : "alert"), h("span", { text }), close);
  }

  for (const topic of ["snapshot", "sessions", "selection", "ui", "connection"]) store.on(topic, render);
  render();
}
