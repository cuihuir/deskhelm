// The selected session's transcript. Each item is one keyed node; a patch
// replaces or extends only its own node, so expanded tools stay open and a
// streaming reply does not redraw the rest.

import { copyText, h, icon, nearBottom } from "../dom.js";
import { diffBlock, looksLikeDiff, renderMarkdown } from "../markdown.js";
import { countdown, historyHint, outcomeText, toolIcon, toolWord } from "../text.js";

const STREAM_RENDER_MS = 50;
const BUSY = new Set(["working", "starting"]);

export function mountTranscript(store, { scroller, root, jump }, actions) {
  const list = h("div", { class: "items" });
  const tail = h("div", { class: "tail" });
  root.replaceChildren(list, tail);
  const nodes = new Map();
  const expanded = new Set();
  const dirty = new Set();
  let key = null;
  let timer = 0;

  jump.addEventListener("click", () => {
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  });
  scroller.addEventListener("scroll", () => {
    if (nearBottom(scroller)) jump.hidden = true;
  });

  // Rendering ---------------------------------------------------------

  function renderAll() {
    key = store.state.selected;
    nodes.clear();
    dirty.clear();
    const session = store.selected();
    if (!session) {
      list.replaceChildren(store.state.sessions.size ? h("div") : hero());
      tail.replaceChildren();
      return;
    }
    list.replaceChildren();
    for (const item of store.items(key).values()) place(item);
    renderTail();
    scroller.scrollTop = scroller.scrollHeight;
    jump.hidden = true;
  }

  function place(item) {
    const node = render(item);
    const old = nodes.get(item.id);
    if (old) old.replaceWith(node);
    else list.append(node);
    nodes.set(item.id, node);
  }

  function render(item) {
    if (item.type === "message") return message(item);
    if (item.type === "tool") return tool(item);
    if (item.type === "approval") return approval(item);
    if (item.type === "input") return input(item);
    if (item.type === "turn") return turn(item);
    return h("div", { hidden: true });
  }

  function message(item) {
    if (item.role === "user") {
      return h("div", { class: "msg user" }, h("div", { class: "bubble", text: item.text }));
    }
    if (item.role === "system") {
      return h("div", { class: "msg system" }, h("p", { text: item.text }));
    }
    const body = h("div", { class: "md" }, renderMarkdown(item.text));
    if (item.streaming && !item.text) body.append(dots());
    const node = h("div", { class: item.streaming ? "msg assistant is-streaming" : "msg assistant" }, body);
    if (!item.streaming && item.text && navigator.clipboard) {
      const copy = h("button", { class: "msg-action", type: "button", title: "复制", "aria-label": "复制这条回复" }, icon("copy"));
      copy.addEventListener("click", async () => {
        if (await copyText(item.text)) actions.announce("已复制");
      });
      node.append(h("div", { class: "msg-actions" }, copy));
    }
    return node;
  }

  function tool(item) {
    const open = expanded.has(`${key}\u001f${item.id}`);
    let status;
    if (item.status === "running") status = h("span", { class: "tool-status" }, h("span", { class: "spinner" }), "运行中");
    else if (item.status === "failed") {
      status = h("span", { class: "tool-status tone-danger" }, icon("cross"), item.exit_code === null ? "失败" : `退出码 ${item.exit_code}`);
    } else status = h("span", { class: "tool-status tone-ok" }, icon("check"), "完成");
    const head = h(
      "button",
      { class: "tool-head", type: "button", "aria-expanded": open ? "true" : "false", on: { click: () => toggle(item.id) } },
      icon(toolIcon(item.kind), "tool-icon"),
      h("span", { class: "tool-kind", text: toolWord(item.kind) }),
      item.title ? h("code", { class: "tool-title", title: item.title, text: item.title }) : h("span", { class: "tool-title" }),
      status,
      icon("chevron", "chevron"),
    );
    const node = h("div", { class: `tool is-${item.status}${open ? " is-open" : ""}` }, head);
    if (open) node.append(toolBody(item));
    return node;
  }

  function toolBody(item) {
    const body = h("div", { class: "tool-body" });
    if (item.truncated) body.append(h("p", { class: "tool-note", text: "前面的输出已省略，这里只保留最后一段。" }));
    if (!item.output) {
      body.append(h("p", { class: "tool-note", text: item.status === "running" ? "还没有输出" : "没有输出" }));
    } else if (item.kind === "file_change" || looksLikeDiff(item.output)) {
      body.append(diffBlock(item.output));
    } else {
      body.append(h("pre", { class: item.stderr ? "output stderr" : "output", text: item.output }));
    }
    return body;
  }

  function toggle(id) {
    const name = `${key}\u001f${id}`;
    if (expanded.has(name)) expanded.delete(name);
    else expanded.add(name);
    const item = store.items(key).get(id);
    if (item) place(item);
  }

  function approval(item) {
    const session = store.selected();
    const ticket = session && session.approvals.find((entry) => entry.approval_id === item.approval_id);
    const decided = store.state.decided.get(item.approval_id);
    const node = h(
      "div",
      { class: ticket ? "approval is-live" : "approval" },
      h(
        "div",
        { class: "approval-head" },
        icon("alert"),
        h("strong", { text: ticket ? "需要批准" : "批准请求" }),
        ticket ? h("span", { class: "approval-time", dataset: { expires: String(ticket.expires_at) }, text: countdown(ticket.expires_at) }) : null,
      ),
      h("p", { class: "approval-summary", text: item.summary || "Agent 请求执行一个操作" }),
    );
    if (ticket && ticket.answerable) {
      const busy = store.state.deciding.has(item.approval_id) || !store.state.connected;
      node.append(h(
        "div",
        { class: "approval-actions" },
        h("button", { class: "button primary", type: "button", disabled: busy, on: { click: () => actions.decide("approve", item.approval_id) } }, icon("check"), "批准"),
        h("button", { class: "button", type: "button", disabled: busy, on: { click: () => actions.decide("reject", item.approval_id) } }, icon("cross"), "拒绝"),
      ));
    } else if (ticket) {
      node.append(h("p", { class: "approval-state", text: "请在 Agent 自己的界面里处理" }));
    } else {
      const text = { approve: "已在这里批准", reject: "已在这里拒绝" }[decided] || "已结束";
      node.append(h("p", { class: "approval-state", text }));
    }
    return node;
  }

  function input(item) {
    return h(
      "div",
      { class: "notice-card" },
      h("div", { class: "notice-head" }, icon("question"), h("strong", { text: "Agent 在提问" })),
      item.prompt ? h("p", { text: item.prompt }) : null,
      h("p", { class: "hint", text: "这个提问要在 Agent 自己的界面里回答。" }),
    );
  }

  function turn(item) {
    return h(
      "div",
      { class: `turn tone-${item.status === "completed" ? "ok" : item.status === "interrupted" ? "muted" : "danger"}` },
      h("span", { class: "turn-text", text: outcomeText(item) }),
      item.message ? h("span", { class: "turn-message", text: item.message }) : null,
    );
  }

  function renderTail() {
    const session = store.selected();
    const nodesInTail = [];
    for (const prompt of store.state.pending.get(key) || []) nodesInTail.push(pending(prompt));
    const items = [...store.items(key).values()];
    const last = items[items.length - 1];
    const streaming = last && last.type === "message" && last.streaming;
    if (session && session.lifecycle === "active" && BUSY.has(session.activity) && !streaming) {
      nodesInTail.push(h("div", { class: "working" }, dots(), session.activity === "starting" ? "正在启动" : "工作中"));
    }
    if (session && !items.length && !nodesInTail.length) {
      nodesInTail.push(h(
        "div",
        { class: "empty" },
        h("p", { class: "empty-title", text: "还没有新的活动" }),
        h("p", { class: "hint", text: `连接之后的消息、工具调用和批准会出现在这里。${historyHint(session)}` }),
      ));
    }
    tail.replaceChildren(...nodesInTail);
  }

  function pending(prompt) {
    const words = { sending: "发送中", sent: "已发送", unknown: "结果未知，请查看 Agent", failed: prompt.reason || "未送达" };
    const note = h("p", { class: "msg-state", text: words[prompt.status] || "" });
    if (prompt.status === "failed" || prompt.status === "unknown") {
      note.append(h("button", {
        class: "link-button", type: "button", text: "移除",
        on: { click: () => store.removePending(key, prompt.commandId) },
      }));
    }
    return h("div", { class: `msg user is-${prompt.status}` }, h("div", { class: "bubble", text: prompt.text }), note);
  }

  function dots() {
    return h("span", { class: "dots", "aria-hidden": "true" }, h("i"), h("i"), h("i"));
  }

  function hero() {
    const commands = ["deskhelm codex", "deskhelm agent claude", "deskhelm agent grok", "deskhelm simulate"];
    return h(
      "div",
      { class: "hero" },
      h("img", { src: "/assets/icon.svg", alt: "", width: "40", height: "40" }),
      h("h2", { text: "还没有会话" }),
      h("p", { text: "在项目目录里启动一个 Agent，它会出现在这里：" }),
      h("div", { class: "commands" }, commands.map((command) => h("code", { text: command }))),
    );
  }

  // Updates -----------------------------------------------------------

  function follow(update) {
    const stick = nearBottom(scroller);
    update();
    if (stick) scroller.scrollTop = scroller.scrollHeight;
    else jump.hidden = false;
  }

  function flushStreaming() {
    timer = 0;
    follow(() => {
      for (const id of dirty) {
        const item = store.items(key).get(id);
        const body = nodes.get(id) && nodes.get(id).querySelector(".md");
        if (item && body) body.replaceChildren(renderMarkdown(item.text));
      }
      dirty.clear();
    });
  }

  store.on("snapshot", renderAll);
  store.on("selection", renderAll);
  store.on("patch", ({ key: changed, op, id }) => {
    if (changed !== key) return;
    const item = store.items(key).get(id);
    if (op === "append" && item && item.type === "message" && nodes.has(id)) {
      dirty.add(id);
      if (!timer) timer = setTimeout(() => requestAnimationFrame(flushStreaming), STREAM_RENDER_MS);
      return;
    }
    follow(() => {
      if (op === "remove") {
        if (nodes.has(id)) nodes.get(id).remove();
        nodes.delete(id);
      } else if (item) {
        dirty.delete(id);
        place(item);
      }
      renderTail();
    });
  });
  const refreshApprovals = () => {
    for (const item of store.items(key).values()) if (item.type === "approval") place(item);
  };
  store.on("sessions", () => {
    if (!store.selected()) {
      renderAll();
      return;
    }
    follow(() => {
      refreshApprovals();
      renderTail();
    });
  });
  store.on("approvals", refreshApprovals);
  store.on("pending", (changed) => {
    if (changed === key) follow(renderTail);
  });
  renderAll();
}
