// The composer: one prompt box, send and stop, and the model menu for
// sessions that can change their model for the next turn.

import { control, sessionFields } from "../api.js";
import { h, icon, newId } from "../dom.js";
import { EFFORT_TEXT, UNKNOWN_OUTCOMES, resultText } from "../text.js";

const BUSY = new Set(["working", "waiting_approval", "waiting_input"]);
const MAX_PROMPT_HEIGHT = 240;

export function mountComposer(store, root, actions) {
  const prompt = h("textarea", { id: "prompt", rows: "1", autocomplete: "off", spellcheck: "false", "aria-label": "给会话的指令" });
  const send = h("button", { class: "send", type: "submit", title: "发送（Enter）", "aria-label": "发送" }, icon("send"));
  const stop = h("button", { class: "stop", type: "button", title: "中断这一轮", "aria-label": "中断这一轮", on: { click: actions.interrupt } }, icon("stop"));
  const chipLabel = h("span", { class: "chip-label" });
  const chip = h("button", { class: "chip", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false" }, icon("sparkle"), chipLabel);
  const menu = h("div", { class: "menu", role: "dialog", "aria-label": "下一轮的模型与推理", hidden: true });
  root.replaceChildren(
    h(
      "div",
      { class: "composer-box" },
      prompt,
      h("div", { class: "composer-row" }, h("div", { class: "composer-left" }, chip), h("div", { class: "composer-right" }, stop, send)),
      menu,
    ),
    h("p", { class: "composer-hint", text: "Enter 发送，Shift+Enter 换行。Agent 在自己界面里的提问仍要在那里回答。" }),
  );

  root.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });
  prompt.addEventListener("keydown", (event) => {
    // keyCode 229 is a key that an input method is still composing.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      submit();
    }
  });
  prompt.addEventListener("input", () => {
    resize();
    sync();
  });
  chip.addEventListener("click", (event) => {
    event.stopPropagation();
    store.setUi("menu");
  });
  menu.addEventListener("click", (event) => event.stopPropagation());
  document.addEventListener("click", () => store.setUi("menu", false));

  function resize() {
    prompt.style.height = "auto";
    prompt.style.height = `${Math.min(prompt.scrollHeight, MAX_PROMPT_HEIGHT)}px`;
  }

  function insert(text) {
    const spacer = prompt.value && !/\s$/.test(prompt.value) ? " " : "";
    prompt.value += spacer + text;
    resize();
    sync();
    if (!prompt.disabled) prompt.focus();
  }

  function state() {
    const session = store.selected();
    const ready = Boolean(session) && session.lifecycle === "active" && store.state.connected && store.state.stream === "open";
    const busy = Boolean(session) && BUSY.has(session.activity);
    const blocked = busy && session.capabilities.prompt_while_busy === "reject";
    return { session, ready, busy, blocked };
  }

  async function submit() {
    const { session, ready, blocked } = state();
    const text = prompt.value;
    if (!ready || blocked || !text.trim()) return;
    const commandId = newId();
    store.addPending(session.key, { commandId, text, status: "sending" });
    prompt.value = "";
    resize();
    sync();
    const result = await control({ kind: "submit_prompt", ...sessionFields(session), command_id: commandId, text });
    const code = result.payload.code || result.payload.error;
    if (result.ok && code === "dispatched") {
      store.updatePending(session.key, commandId, { status: "sent" });
    } else if (UNKNOWN_OUTCOMES.has(code)) {
      store.updatePending(session.key, commandId, { status: "unknown" });
    } else {
      store.updatePending(session.key, commandId, { status: "failed", reason: resultText(result.payload, result.ok) });
      // Nothing reached the agent, so give the text back.
      if (!prompt.value && store.state.selected === session.key) insert(text);
    }
    actions.announce(resultText(result.payload, result.ok));
  }

  function sync() {
    const { session, ready, busy, blocked } = state();
    prompt.disabled = !ready;
    if (!session) prompt.placeholder = "先选择一个会话";
    else if (session.lifecycle !== "active") prompt.placeholder = "这个会话已断开";
    else if (!ready) prompt.placeholder = "正在连接…";
    else if (blocked) prompt.placeholder = `${session.label} 正在工作，这一轮结束后再发送`;
    else prompt.placeholder = `给 ${session.label} 发送指令`;
    send.disabled = !ready || blocked || !prompt.value.trim();
    stop.hidden = !(ready && busy && session.capabilities.interrupt);
    const configurable = Boolean(session) && Array.isArray(session.capabilities.configure) && session.capabilities.configure.length > 0;
    chip.hidden = !configurable;
    if (configurable && ready) actions.ensureCatalog(session);
    if (configurable) chipLabel.textContent = choiceLabel(store.state.catalogs.get(session.key));
    chip.setAttribute("aria-expanded", store.state.ui.menu ? "true" : "false");
    menu.hidden = !(configurable && store.state.ui.menu);
    if (!menu.hidden) paintMenu(session);
  }

  // Model menu --------------------------------------------------------

  function choiceLabel(entry) {
    const data = entry && entry.status === "ready" ? entry.data : null;
    if (!data || !data.model) return "模型";
    const model = (data.models || []).find((item) => item && item.id === data.model);
    const name = model ? model.name || model.id : data.model;
    return data.reasoning_effort ? `${name} · ${EFFORT_TEXT[data.reasoning_effort] || data.reasoning_effort}` : name;
  }

  function paintMenu(session) {
    const entry = store.state.catalogs.get(session.key);
    const content = [h("div", { class: "menu-title", text: "下一轮使用" })];
    if (!entry && !session.catalog_rev) {
      content.push(h("p", { class: "hint", text: "这个会话还没有提供模型列表。" }));
    } else if (!entry || entry.status === "loading") {
      content.push(h("p", { class: "hint", text: "正在读取模型…" }));
    } else if (entry.status !== "ready" || !entry.data.available) {
      content.push(h("p", { class: "hint", text: "这个会话暂时列不出模型。" }));
    } else {
      const data = entry.data;
      const models = (data.models || []).filter((item) => item && typeof item.id === "string");
      const current = models.find((item) => item.id === data.model);
      content.push(
        h("div", { class: "menu-label", text: "模型" }),
        h("div", { class: "options", role: "radiogroup", "aria-label": "模型" }, models.map((model) => h(
          "button",
          {
            class: "option", type: "button", role: "radio", "aria-checked": model.id === data.model ? "true" : "false",
            disabled: entry.applying, on: { click: () => choose(session, model, data) },
          },
          h("span", { text: model.name || model.id }),
          model.is_default ? h("span", { class: "option-note", text: "默认" }) : null,
        ))),
      );
      const efforts = current && Array.isArray(current.efforts) ? current.efforts.filter((item) => item && typeof item.id === "string") : [];
      if (efforts.length) {
        content.push(
          h("div", { class: "menu-label", text: "推理强度" }),
          h("div", { class: "segmented", role: "radiogroup", "aria-label": "推理强度" }, efforts.map((effort) => h("button", {
            type: "button", role: "radio", "aria-checked": effort.id === data.reasoning_effort ? "true" : "false",
            title: effort.description || "", disabled: entry.applying,
            text: EFFORT_TEXT[effort.id] || effort.id,
            on: { click: () => actions.configure(session, current.id, effort.id) },
          }))),
        );
      }
      if (!models.length) content.push(h("p", { class: "hint", text: "没有可切换的模型。" }));
    }
    content.push(h("p", { class: "menu-note", text: "只用于下一轮，不会写入 Agent 的全局配置。" }));
    menu.replaceChildren(...content);
  }

  function choose(session, model, data) {
    const ids = Array.isArray(model.efforts) ? model.efforts.map((item) => item && item.id) : [];
    let effort = "";
    if (data.reasoning_effort && ids.includes(data.reasoning_effort)) effort = data.reasoning_effort;
    else if (model.default_effort && ids.includes(model.default_effort)) effort = model.default_effort;
    actions.configure(session, model.id, effort);
  }

  for (const topic of ["snapshot", "sessions", "selection", "connection", "ui", "catalog"]) store.on(topic, sync);
  store.on("selection", () => store.setUi("menu", false));
  sync();
  return { insert, focus: () => prompt.focus() };
}
