// The details panel: what the selected session is, what it can do, and its
// catalog of models, MCP servers, and skills. The catalog loads when the panel
// opens and on refresh; it is never polled.

import { copyText, h, icon } from "../dom.js";
import { EFFORT_TEXT, MCP_STATUS, activityText, capabilityWords } from "../text.js";
import { runtimeLine } from "./header.js";

export function mountDetails(store, root, actions) {
  const body = h("div", { class: "details-body" });
  const refresh = h("button", { class: "icon-button", type: "button", title: "刷新", "aria-label": "刷新目录", on: { click: () => load(true) } }, icon("refresh"));
  root.replaceChildren(
    h(
      "header",
      { class: "details-head" },
      h("h2", { text: "会话详情" }),
      refresh,
      h("button", { class: "icon-button", type: "button", title: "关闭", "aria-label": "关闭详情", on: { click: () => store.setUi("details", false) } }, icon("close")),
    ),
    body,
  );

  function load(force) {
    const session = store.selected();
    if (!session || !store.state.ui.details) return;
    if (force) actions.loadCatalog(session);
    else actions.ensureCatalog(session);
  }

  function row(label, value, copyable) {
    const cell = h("dd", null, value);
    if (copyable && navigator.clipboard) {
      cell.append(h("button", {
        class: "icon-button small", type: "button", title: "复制", "aria-label": `复制${label}`,
        on: { click: async () => { if (await copyText(copyable)) actions.announce("已复制"); } },
      }, icon("copy")));
    }
    return [h("dt", { text: label }), cell];
  }

  function section(title, ...children) {
    return h("section", { class: "details-section" }, h("h3", { text: title }), ...children);
  }

  function render() {
    const session = store.selected();
    root.hidden = !store.state.ui.details;
    if (!session || root.hidden) {
      body.replaceChildren();
      return;
    }
    const opened = session.opened_at ? new Date(session.opened_at).toLocaleString("zh-CN", { hour12: false }) : "";
    const sections = [
      section(
        "概况",
        h(
          "dl",
          { class: "facts" },
          row("Agent", session.label),
          row("运行时", runtimeLine(session) || "未知"),
          row("项目", session.project_id),
          row("会话", h("code", { text: session.session_id }), session.session_id),
          row("状态", activityText(session)),
          opened ? row("打开于", opened) : [],
          row("语音", store.state.focus === session.key ? "按住右 Alt 说的话会发给这个会话" : "没有聚焦这个会话"),
        ),
      ),
      section("能力", h("div", { class: "chips" }, capabilityWords(session.capabilities).map((word) => h("span", { class: "tag", text: word })))),
    ];
    sections.push(...catalogSections(session));
    body.replaceChildren(...sections);
  }

  function catalogSections(session) {
    const entry = store.state.catalogs.get(session.key);
    if (!entry && !session.catalog_rev) return [section("目录", h("p", { class: "hint", text: "这个会话还没有提供模型、MCP 和 skills。" }))];
    if (!entry || entry.status === "loading") return [section("目录", h("p", { class: "hint", text: "正在读取模型、MCP 和 skills…" }))];
    const data = entry.data || {};
    if (entry.status !== "ready" || !data.available) {
      return [section("目录", h("p", { class: "hint", text: "这个会话还不能列出模型、MCP 和 skills。" }))];
    }
    const sections = [];
    if (data.model) {
      const model = (data.models || []).find((item) => item && item.id === data.model);
      const effort = data.reasoning_effort ? ` · 推理 ${EFFORT_TEXT[data.reasoning_effort] || data.reasoning_effort}` : "";
      sections.push(section("模型", h("p", { text: `${model ? model.name || model.id : data.model}${effort}` })));
    }
    const servers = Array.isArray(data.mcp_servers) ? data.mcp_servers.filter((item) => item && typeof item.name === "string") : [];
    if (servers.length || data.mcp_notice) {
      sections.push(section("MCP", data.mcp_notice === "unavailable"
        ? h("p", { class: "hint", text: "这一项暂时取不到。" })
        : h("ul", { class: "catalog" }, servers.map((server) => h(
          "li", null, h("span", { class: "name", text: server.name, title: server.name }),
          h("span", { class: `tag tone-${server.status === "connected" ? "ok" : server.status === "failed" ? "danger" : "muted"}`, text: MCP_STATUS[server.status] || "未知" }),
        )))));
    }
    const skills = Array.isArray(data.skills) ? data.skills.filter((item) => item && typeof item.name === "string" && /^\S+$/.test(item.name)) : [];
    if (skills.length || data.skills_notice) {
      sections.push(section("Skills", data.skills_notice === "unavailable"
        ? h("p", { class: "hint", text: "这一项暂时取不到。" })
        : h("ul", { class: "catalog" }, skills.map((skill) => h("li", null, skill.enabled
          ? h("button", { class: "link-button name", type: "button", title: `插入 $${skill.name}`, text: skill.name, on: { click: () => actions.insertText(`$${skill.name} `) } })
          : h("span", { class: "name is-off", text: skill.name, title: "已停用" })))),
        h("p", { class: "hint", text: "点一下会把 skill 插入到输入框。" })));
    }
    const declared = Array.isArray(data.agent_capabilities) ? data.agent_capabilities.filter((name) => typeof name === "string" && /^\S+$/.test(name)) : [];
    if (declared.length) {
      sections.push(section("Agent 声明", h("div", { class: "chips" }, declared.map((name) => h("span", { class: "tag", text: name })))));
    }
    if (data.truncated) sections.push(h("p", { class: "hint", text: "目录太长，只显示了一部分。" }));
    return sections;
  }

  store.on("ui", () => {
    load(false);
    render();
  });
  store.on("selection", () => {
    load(false);
    render();
  });
  store.on("sessions", () => {
    load(false);
    render();
  });
  for (const topic of ["snapshot", "catalog"]) store.on(topic, render);
  render();
}
