(function () {
  "use strict";

  var THEME_KEY = "deskhelm-web-theme";
  var SELECTION_KEY = "deskhelm-web-selection";
  var SEPARATOR = "\u001f";

  var STATE_TEXT = {
    offline: "离线",
    idle: "空闲",
    thinking: "思考中",
    running_tool: "执行工具",
    working: "工作中",
    waiting_approval: "等待批准",
    waiting_user: "等待输入",
    completed: "已完成",
    failed: "失败",
    active: "在线",
    disconnected: "已断开",
    attention: "需要关注"
  };

  var CODE_TEXT = {
    focused: "已聚焦这个会话",
    dispatched: "指令已交给 Bridge",
    expired: "这个请求已过期",
    target_not_found: "找不到这个会话",
    target_inactive: "这个会话不在活动状态",
    target_not_focused: "需要先聚焦这个会话",
    handler_unavailable: "Agent 当前无法接收这个操作",
    approval_not_found: "找不到这条批准请求",
    approval_already_decided: "这条批准已经处理过",
    approval_summary_mismatch: "批准摘要已经变化",
    approval_target_mismatch: "批准目标不匹配",
    approval_expiry_mismatch: "批准期限不匹配",
    issuer_mismatch: "控制来源不匹配",
    idempotency_conflict: "重复的操作冲突",
    idempotency_capacity: "操作记录已满",
    dispatch_failed: "操作没有完成",
    invalid_control_request: "这个操作无法发送",
    bridge_unavailable: "Bridge 暂时不可用",
    too_many_clients: "打开这个页面的客户端太多",
    invalid_catalog_request: "这个能力请求无法发送"
  };

  var CATALOG_NOTICE = {
    catalog_unavailable: "这个会话还不能列出能力。",
    catalog_failed: "这次没有取到能力列表。",
    target_not_found: "找不到这个会话",
    target_inactive: "这个会话不在活动状态"
  };

  var CAPABILITY_WORD = {
    state_events: "状态",
    interaction_events: "输出",
    tool_events: "工具",
    approval_requests: "审批",
    session_resume: "恢复",
    submit_prompt: "指令",
    interrupt: "中断",
    approval_decisions: "决定"
  };

  var MCP_STATUS = {
    notStarted: "未启动",
    starting: "启动中",
    connected: "已连接",
    authenticationRequired: "需要登录",
    failed: "失败",
    cancelled: "已取消",
    disabled: "已停用",
    unknown: "未知"
  };

  var EFFORT_LABEL = {
    minimal: "极低",
    none: "无",
    low: "低",
    medium: "中",
    high: "高",
    xhigh: "很高",
    max: "最高"
  };

  var catalog = {
    panel: "",
    showModels: false,
    token: 0,
    applyToken: 0,
    key: "",
    data: null,
    serverModel: "",
    loading: false,
    applying: false,
    pick: null,
    flash: ""
  };

  var NOTICE_TEXT = {
    "bridge unavailable": "没有连上 Bridge。请使用和 deskhelm codex --tui 相同的套接字",
    "interaction stream restarted": "实时活动已重新连接，更早的内容不会补上",
    "interaction event exceeded the web client bound": "有一条活动太大，页面没有收下"
  };

  var state = {
    epoch: "",
    connected: false,
    notice: "",
    sessions: [],
    events: [],
    focused: null,
    selected: "",
    query: "",
    optimistic: [],
    sending: false,
    deciding: "",
    listSig: "",
    bodySig: "",
    stick: true,
    source: null
  };

  var threads = document.getElementById("threads");
  var transcript = document.getElementById("transcript");
  var approvalSlot = document.getElementById("approval");
  var review = document.getElementById("review");
  var reviewBody = document.getElementById("review-body");
  var search = document.getElementById("search");
  var prompt = document.getElementById("prompt");
  var composer = document.getElementById("composer");
  var sidebar = document.getElementById("sidebar");
  var scrim = document.getElementById("scrim");
  var live = document.getElementById("live");
  var connection = document.getElementById("connection");
  var title = document.getElementById("title");
  var subtitle = document.getElementById("subtitle");
  var agentPill = document.getElementById("agent-pill");
  var statusNode = document.getElementById("status");
  var focusButton = document.getElementById("focus");
  var interruptButton = document.getElementById("interrupt");
  var capability = document.getElementById("capability");
  var capabilityToggle = document.getElementById("capability-toggle");
  var capabilityNote = document.getElementById("capability-note");
  var toolsNote = document.getElementById("tools-note");
  var modelMenu = document.getElementById("model-menu");
  var modelButton = document.getElementById("model-button");
  var effortButton = document.getElementById("effort-button");
  var effortLabelNode = document.getElementById("effort-label");
  var modelLine = document.getElementById("model-line");
  var modelLineLabel = document.getElementById("model-line-label");
  var modelList = document.getElementById("model-list");
  var effortTrack = document.getElementById("effort-track");
  var clearChoiceButton = document.getElementById("clear-choice");
  var mcpList = document.getElementById("mcp-list");
  var skillList = document.getElementById("skill-list");
  var agentCapabilityList = document.getElementById("agent-capability-list");
  var reviewToggle = document.getElementById("review-toggle");
  var themeButton = document.getElementById("theme");
  var sendButton = document.getElementById("send");

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key) || window.sessionStorage.getItem(key) || "";
    } catch (error) {
      return "";
    }
  }

  function storageSet(store, key, value) {
    try {
      store.setItem(key, value);
    } catch (error) {
      return;
    }
  }

  function announce(text) {
    if (!text) return;
    live.textContent = text;
  }

  function codeText(code) {
    if (Object.prototype.hasOwnProperty.call(CODE_TEXT, code)) return CODE_TEXT[code];
    if (typeof code === "string" && /^[a-z0-9_]+$/.test(code)) return code;
    return "";
  }

  function noticeText() {
    if (!state.connected) {
      if (Object.prototype.hasOwnProperty.call(NOTICE_TEXT, state.notice)) {
        return NOTICE_TEXT[state.notice];
      }
      return "正在连接 Bridge";
    }
    var connected = "已连接 · " + state.sessions.length + " 个会话";
    if (!state.notice) return connected;
    if (Object.prototype.hasOwnProperty.call(NOTICE_TEXT, state.notice)) {
      return NOTICE_TEXT[state.notice];
    }
    if (/^[a-z0-9 _]+$/.test(state.notice)) return state.notice;
    return connected;
  }

  function relativeTime(ms) {
    if (!ms) return "";
    var delta = Date.now() - Number(ms);
    if (delta < 60000) return "刚刚";
    if (delta < 3600000) return Math.floor(delta / 60000) + " 分钟前";
    if (delta < 86400000) return Math.floor(delta / 3600000) + " 小时前";
    return new Date(Number(ms)).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  }

  function statusOf(session) {
    if (session.agent_state && STATE_TEXT[session.agent_state]) {
      return { code: session.agent_state, label: STATE_TEXT[session.agent_state] };
    }
    if (session.approval) return { code: "waiting_approval", label: "等待批准" };
    if (session.attention) return { code: "attention", label: "需要关注" };
    if (session.lifecycle && STATE_TEXT[session.lifecycle]) {
      return { code: session.lifecycle, label: STATE_TEXT[session.lifecycle] };
    }
    return { code: "offline", label: "离线" };
  }

  function selectedSession() {
    for (var i = 0; i < state.sessions.length; i += 1) {
      if (state.sessions[i].key === state.selected) return state.sessions[i];
    }
    return null;
  }

  function isFocused(session) {
    if (!session || !state.focused) return false;
    return state.focused.agent_id === session.agent_id
      && state.focused.session_id === session.session_id
      && state.focused.project_id === session.project_id;
  }

  function sessionEvents() {
    return state.events.filter(function (event) {
      return event.key === state.selected;
    });
  }

  function ensureSelection() {
    var keys = {};
    state.sessions.forEach(function (session) { keys[session.key] = true; });
    if (state.selected && keys[state.selected]) return;
    var stored = "";
    try {
      stored = window.sessionStorage.getItem(SELECTION_KEY) || "";
    } catch (error) {
      stored = "";
    }
    if (stored && keys[stored]) {
      state.selected = stored;
      return;
    }
    if (state.focused) {
      var focusedKey = [state.focused.agent_id, state.focused.session_id, state.focused.project_id].join(SEPARATOR);
      if (keys[focusedKey]) {
        state.selected = focusedKey;
        return;
      }
    }
    var newest = state.sessions.slice().sort(function (a, b) {
      return (b.updated_at || 0) - (a.updated_at || 0);
    })[0];
    state.selected = newest ? newest.key : "";
  }

  function selectSession(key) {
    state.selected = key;
    state.stick = true;
    try {
      window.sessionStorage.setItem(SELECTION_KEY, key);
    } catch (error) {
      /* selection still works for this page load */
    }
    if (window.matchMedia("(max-width: 920px)").matches) closeDrawers();
    render();
  }

  function groupedSessions() {
    var query = state.query.trim().toLowerCase();
    var buckets = new Map();
    state.sessions.forEach(function (session) {
      var haystack = [session.label, session.session_id, session.project_id, session.agent_id, session.runtime_name].join("\n").toLowerCase();
      if (query && haystack.indexOf(query) === -1) return;
      var list = buckets.get(session.project_id) || [];
      list.push(session);
      buckets.set(session.project_id, list);
    });
    var groups = [];
    buckets.forEach(function (list, project) {
      list.sort(function (a, b) { return (b.updated_at || 0) - (a.updated_at || 0); });
      groups.push({ project: project, list: list, newest: list[0].updated_at || 0 });
    });
    groups.sort(function (a, b) { return b.newest - a.newest; });
    return groups;
  }

  function renderThreads() {
    var groups = groupedSessions();
    threads.replaceChildren();
    if (!state.sessions.length) {
      threads.append(el("p", "empty", "还没有会话。先在项目目录运行 deskhelm codex --tui，或 deskhelm agent claude、deskhelm agent grok。会话注册后会出现在这里。"));
      return;
    }
    if (!groups.length) {
      threads.append(el("p", "empty", "没有匹配的会话。"));
      return;
    }
    groups.forEach(function (group) {
      threads.append(el("div", "project", group.project));
      group.list.forEach(function (session) {
        var status = statusOf(session);
        var button = el("button", "thread");
        button.type = "button";
        if (session.key === state.selected) button.setAttribute("aria-current", "true");
        var dot = el("span", "dot " + status.code);
        var text = el("span");
        text.append(el("b", null, session.label || session.session_id));
        text.append(el("small", null, status.label));
        var meta = runtimeMeta(session);
        if (meta) text.append(el("small", "thread-meta", meta));
        var preview = latestLine(session);
        if (preview) text.append(el("small", "thread-meta thread-preview", preview));
        var time = el("small");
        time.dataset.time = String(session.updated_at || 0);
        time.textContent = relativeTime(session.updated_at);
        button.append(dot, text, time);
        button.addEventListener("click", function () { selectSession(session.key); });
        threads.append(button);
      });
    });
  }

  function buildItems(events) {
    var items = [];
    var messages = new Map();
    var tools = new Map();
    events.forEach(function (event) {
      var payload = event.payload || {};
      if (event.kind === "message") {
        var messageId = event.correlation_id || event.event_id;
        var message = messages.get(messageId);
        if (!message) {
          message = { type: "message", id: messageId, role: payload.role || "assistant", text: "", phase: "" };
          messages.set(messageId, message);
          items.push(message);
        }
        message.role = payload.role || message.role;
        if (payload.phase === "complete" && payload.text) {
          message.text = payload.text;
          message.phase = "complete";
        } else if (payload.phase === "delta" && message.phase !== "complete") {
          message.text += payload.text || "";
          message.phase = "delta";
        } else if (!message.text && payload.text) {
          message.text = payload.text;
        }
      } else if (event.kind === "tool") {
        var toolId = payload.tool_call_id || event.correlation_id || event.event_id;
        var tool = tools.get(toolId);
        if (!tool) {
          tool = { type: "tool", id: toolId, name: payload.name || "tool", output: "", phase: "", exitCode: null, stream: "" };
          tools.set(toolId, tool);
          items.push(tool);
        }
        tool.name = payload.name || tool.name;
        if (payload.phase === "output" && payload.text) {
          tool.output += payload.text;
          tool.stream = payload.stream || tool.stream;
        }
        if (payload.phase === "complete") {
          tool.phase = "complete";
          if (typeof payload.exit_code === "number") tool.exitCode = payload.exit_code;
        } else if (tool.phase !== "complete") {
          tool.phase = payload.phase || tool.phase;
        }
      } else if (event.kind === "user_input_required") {
        items.push({ type: "input", id: event.event_id, prompt: payload.prompt || "" });
      } else if (event.kind === "task_completed" || event.kind === "task_failed") {
        items.push({
          type: "task",
          id: event.event_id,
          failed: event.kind === "task_failed",
          message: payload.message || "",
          code: payload.error_code || ""
        });
      }
    });
    return items;
  }

  function looksLikeDiff(text) {
    return text.split("\n").some(function (line) {
      return line.indexOf("@@") === 0 || (line.charAt(0) === "+" && line.indexOf("+++") !== 0) || (line.charAt(0) === "-" && line.indexOf("---") !== 0);
    });
  }

  function appendOutput(parent, text) {
    if (!text) return;
    if (!looksLikeDiff(text)) {
      parent.append(el("pre", "output", text));
      return;
    }
    var pre = el("pre", "diff");
    text.split("\n").forEach(function (line) {
      var row = el("div", null, line || " ");
      if (line.indexOf("@@") === 0) row.className = "hunk";
      else if (line.indexOf("+") === 0 && line.indexOf("+++") !== 0) row.className = "add";
      else if (line.indexOf("-") === 0 && line.indexOf("---") !== 0) row.className = "del";
      pre.append(row);
    });
    parent.append(pre);
  }

  function toolCard(tool) {
    var card = el("article", "card");
    var label = tool.phase === "complete" ? "已完成" : "进行中";
    if (typeof tool.exitCode === "number" && tool.exitCode !== 0) label = "失败";
    card.append(el("p", "kicker", label));
    card.append(el("h3", null, tool.name || "tool"));
    appendOutput(card, tool.output);
    if (typeof tool.exitCode === "number") card.append(el("p", "hint", "退出码 " + tool.exitCode));
    if (tool.stream === "stderr") card.append(el("p", "hint", "错误输出"));
    return card;
  }

  function helmMark() {
    var mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    mark.setAttribute("viewBox", "0 0 24 24");
    mark.setAttribute("class", "mark");
    mark.setAttribute("aria-hidden", "true");
    ["M4 14.2 12 4.2l8 10v2.1c0 2.1-3.5 3.7-8 3.7s-8-1.6-8-3.7Z", "M8.2 15.2c1.1.7 2.4 1 3.8 1s2.7-.3 3.8-1"].forEach(function (d) {
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      mark.append(path);
    });
    return mark;
  }

  function latestLine(session) {
    var items = buildItems(state.events.filter(function (event) {
      return event.key === session.key;
    }));
    for (var index = items.length - 1; index >= 0; index -= 1) {
      var item = items[index];
      if (item.type === "message" && item.text) {
        return item.text.replace(/\s+/g, " ").trim().slice(0, 160);
      }
      if (item.type === "task") return item.failed ? "这一轮失败" : "这一轮完成";
      if (item.type === "tool" && item.name) return item.name;
    }
    return "";
  }

  function historyHint(session) {
    var runtime = session && session.runtime_name;
    if (runtime === "claude-code" || runtime === "grok") {
      return "这里只显示本次打开页面之后的活动。";
    }
    return "这里只显示本次打开页面之后的活动。更早的对话仍在 Codex 里。";
  }

  function appendStage(column, line, detail, session) {
    column.classList.add("is-empty");
    var stage = el("div", "stage");
    stage.append(helmMark());
    stage.append(el("p", "stage-line", line));
    if (detail) stage.append(el("p", "hint", detail));
    stage.append(el("p", "hint", historyHint(session)));
    column.append(stage);
  }

  function renderTranscript() {
    var session = selectedSession();
    var column = el("div", "column");
    if (!session) {
      appendStage(column, "还没有会话", "先在项目目录运行 deskhelm codex --tui。");
    } else {
      var items = buildItems(sessionEvents());
      if (!items.length && !state.optimistic.length) {
        appendStage(column, "这个会话还没有新的活动", "连接之后的消息、工具和批准会出现在这里。", session);
      }
      items.forEach(function (item) {
        if (item.type === "message") {
          if (!item.text) return;
          var role = item.role === "user" ? "user" : (item.role === "system" ? "system" : "assistant");
          var row = el("div", "row " + role);
          if (role === "assistant") {
            var mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
            mark.setAttribute("viewBox", "0 0 24 24");
            mark.setAttribute("class", "mark avatar");
            mark.setAttribute("aria-hidden", "true");
            var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", "M4 14.2 12 4.2l8 10v2.1c0 2.1-3.5 3.7-8 3.7s-8-1.6-8-3.7Z");
            mark.append(path);
            row.append(mark);
          }
          row.append(el("div", "bubble", item.text));
          column.append(row);
        } else if (item.type === "tool") {
          column.append(toolCard(item));
        } else if (item.type === "input") {
          var input = el("article", "card");
          input.append(el("p", "kicker", "需要在 Codex 里回答"));
          if (item.prompt) input.append(el("p", null, item.prompt));
          input.append(el("p", "hint", "这个提问要在 Codex 终端里完成。下面的输入框只会发送普通会话指令。"));
          column.append(input);
        } else if (item.type === "task") {
          var task = el("p", "task", item.failed ? "这一轮失败" : "这一轮完成");
          if (item.message) task.textContent += "：" + item.message;
          if (item.code) task.textContent += "（" + item.code + "）";
          column.append(task);
        }
      });
      state.optimistic.forEach(function (item) {
        var pending = el("div", "row user");
        var bubble = el("div", "bubble" + (item.failed ? " failed" : " pending"), item.text);
        pending.append(bubble);
        if (item.failed) pending.append(el("p", "hint failed", "未送达"));
        column.append(pending);
      });
    }
    var near = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
    transcript.replaceChildren(column);
    if (state.stick || near) transcript.scrollTop = transcript.scrollHeight;
  }

  function renderApproval() {
    var session = selectedSession();
    var pending = session && session.approval;
    approvalSlot.replaceChildren();
    if (!pending || pending.expires_at <= Date.now()) return;
    var card = el("article", "card approval");
    card.append(el("p", "kicker", "等待批准"));
    card.append(el("p", null, pending.summary));
    var remain = el("p", "hint");
    remain.dataset.expires = String(pending.expires_at);
    remain.textContent = "剩余时间更新中";
    card.append(remain);
    var actions = el("div", "approval-actions");
    var allow = el("button", "primary", "批准");
    var deny = el("button", null, "拒绝");
    allow.type = "button";
    deny.type = "button";
    allow.disabled = state.deciding === pending.request_id;
    deny.disabled = allow.disabled;
    allow.addEventListener("click", function () { decide("approve", pending.request_id); });
    deny.addEventListener("click", function () { decide("reject", pending.request_id); });
    actions.append(allow, deny);
    card.append(actions);
    approvalSlot.append(card);
    tick();
  }

  function renderReview() {
    var tools = buildItems(sessionEvents()).filter(function (item) { return item.type === "tool"; });
    reviewBody.replaceChildren();
    if (!tools.length) {
      reviewBody.append(el("p", "empty", "这个会话还没有工具或文件活动。"));
      return;
    }
    tools.forEach(function (tool) { reviewBody.append(toolCard(tool)); });
  }

  function syncChrome() {
    var session = selectedSession();
    var focused = isFocused(session);
    title.textContent = session ? (session.label || session.session_id) : "DeskHelm";
    subtitle.textContent = session ? session.project_id : "选择一个已注册的会话";
    if (session) {
      agentPill.hidden = false;
      agentPill.textContent = session.runtime_name
        ? session.runtime_name + " · " + session.agent_id
        : session.agent_id;
      var status = statusOf(session);
      statusNode.hidden = false;
      statusNode.textContent = status.label;
    } else {
      agentPill.hidden = true;
      statusNode.hidden = true;
    }
    focusButton.textContent = focused ? "已聚焦" : "聚焦语音";
    focusButton.disabled = !session || !state.connected || focused;
    interruptButton.disabled = !session || !state.connected;
    syncChoiceLabels();
    prompt.disabled = !session || !state.connected || state.sending;
    sendButton.disabled = prompt.disabled;
    connection.textContent = noticeText();
    reviewToggle.setAttribute("aria-expanded", review.hidden ? "false" : "true");
  }

  function listSignature() {
    return JSON.stringify({
      query: state.query,
      selected: state.selected,
      latest: state.events.length ? state.events[state.events.length - 1].event_id : "",
      sessions: state.sessions.map(function (session) {
        return [
          session.key,
          session.label,
          session.agent_state,
          session.lifecycle,
          session.attention,
          session.approval ? session.approval.request_id : "",
          session.updated_at,
          session.runtime_name || "",
          Array.isArray(session.capabilities) ? session.capabilities.join(",") : ""
        ];
      })
    });
  }

  function bodySignature() {
    var session = selectedSession();
    return JSON.stringify({
      selected: state.selected,
      events: sessionEvents().map(function (event) {
        var payload = event.payload || {};
        return [event.event_id, event.kind, payload.phase || "", payload.text ? payload.text.length : 0, payload.exit_code];
      }),
      optimistic: state.optimistic,
      approval: session && session.approval ? session.approval.request_id + ":" + session.approval.expires_at : "",
      deciding: state.deciding
    });
  }

  function render() {
    ensureSelection();
    syncCatalogSelection();
    var nextList = listSignature();
    if (nextList !== state.listSig) {
      state.listSig = nextList;
      renderThreads();
    }
    var nextBody = bodySignature();
    if (nextBody !== state.bodySig) {
      state.bodySig = nextBody;
      renderTranscript();
      renderApproval();
      renderReview();
    }
    syncChrome();
  }

  function tick() {
    var now = Date.now();
    document.querySelectorAll("[data-expires]").forEach(function (node) {
      var left = Number(node.dataset.expires) - now;
      if (left <= 0) {
        node.textContent = "已过期";
        var card = node.closest(".approval");
        if (card) card.querySelectorAll("button").forEach(function (button) { button.disabled = true; });
      } else {
        var seconds = Math.ceil(left / 1000);
        node.textContent = seconds >= 60 ? Math.ceil(seconds / 60) + " 分钟内有效" : seconds + " 秒内有效";
      }
    });
    document.querySelectorAll("[data-time]").forEach(function (node) {
      node.textContent = relativeTime(node.dataset.time);
    });
  }

  function applySessions(data) {
    if (!data || typeof data !== "object") return;
    if (data.epoch && data.epoch !== state.epoch) {
      state.epoch = data.epoch;
      state.events = [];
      state.optimistic = [];
      state.listSig = "";
      state.bodySig = "";
    }
    state.connected = Boolean(data.connected);
    state.notice = typeof data.notice === "string" ? data.notice : "";
    state.focused = data.focused_session || null;
    if (Array.isArray(data.sessions)) state.sessions = data.sessions;
    ensureSelection();
  }

  function applySnapshot(data) {
    var previous = state.events.slice();
    applySessions(data);
    if (data && Array.isArray(data.events)) {
      var seen = {};
      data.events.forEach(function (event) {
        if (event && event.event_id) seen[event.event_id] = true;
      });
      var localOnly = previous.filter(function (event) {
        return event.event_id && !seen[event.event_id];
      });
      state.events = data.events.concat(localOnly);
    }
    render();
    announce(state.notice ? noticeText() : "");
  }

  function applyInteraction(event) {
    if (!event || !event.event_id) return;
    if (state.events.some(function (item) { return item.event_id === event.event_id; })) return;
    state.events.push(event);
    var payload = event.payload || {};
    if (event.kind === "message" && payload.role === "user" && payload.phase === "complete") {
      var index = state.optimistic.findIndex(function (item) {
        return item.text === payload.text && !item.failed;
      });
      if (index >= 0) state.optimistic.splice(index, 1);
    }
    render();
  }

  function parseEvent(raw) {
    try {
      return JSON.parse(raw);
    } catch (error) {
      return null;
    }
  }

  function connectEvents() {
    if (state.source) state.source.close();
    var source = new EventSource("/api/events");
    state.source = source;
    source.addEventListener("snapshot", function (event) { applySnapshot(parseEvent(event.data)); });
    source.addEventListener("sessions", function (event) {
      applySessions(parseEvent(event.data));
      render();
    });
    source.addEventListener("interaction", function (event) { applyInteraction(parseEvent(event.data)); });
    source.onerror = function () {
      state.connected = false;
      render();
    };
  }

  function control(body) {
    return fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        return { ok: response.ok, payload: payload };
      });
    }).catch(function () {
      return { ok: false, payload: { error: "bridge_unavailable" } };
    });
  }

  function describe(result) {
    var payload = result.payload || {};
    // A v2 server sends the shared wording; the v1 server sends codes only.
    if (typeof payload.message === "string" && payload.message) return payload.message;
    return codeText(payload.code) || codeText(payload.error) || (result.ok ? "已发送" : "没有完成");
  }

  function sendSimple(kind) {
    var session = selectedSession();
    if (!session || !state.connected) return;
    control({
      kind: kind,
      agent_id: session.agent_id,
      session_id: session.session_id,
      project_id: session.project_id
    }).then(function (result) {
      announce(describe(result));
      render();
    });
  }

  function decide(kind, requestId) {
    var session = selectedSession();
    if (!session || state.deciding) return;
    state.deciding = requestId;
    render();
    control({
      kind: kind,
      agent_id: session.agent_id,
      session_id: session.session_id,
      project_id: session.project_id,
      request_id: requestId
    }).then(function (result) {
      state.deciding = "";
      announce(describe(result));
      render();
    });
  }

  function submitPrompt(event) {
    event.preventDefault();
    var session = selectedSession();
    var text = prompt.value;
    if (!session || !text.trim() || state.sending || !state.connected) return;
    if (catalog.panel) setPanel("");
    state.sending = true;
    var optimistic = { id: String(Date.now()), text: text, failed: false };
    state.optimistic.push(optimistic);
    prompt.value = "";
    resizePrompt();
    state.stick = true;
    render();
    control({
      kind: "submit_prompt",
      agent_id: session.agent_id,
      session_id: session.session_id,
      project_id: session.project_id,
      text: text
    }).then(function (result) {
      state.sending = false;
      var code = result.payload && (result.payload.code || result.payload.error);
      if (!result.ok || (code && code !== "dispatched")) optimistic.failed = true;
      announce(describe(result));
      render();
    });
  }

  function resizePrompt() {
    prompt.style.height = "auto";
    prompt.style.height = Math.min(prompt.scrollHeight, 180) + "px";
  }

  function setReview(open) {
    if (open && catalog.panel) setPanel("");
    review.hidden = !open;
    review.classList.toggle("is-open", open);
    reviewToggle.setAttribute("aria-expanded", open ? "true" : "false");
    syncScrim();
  }

  function setSidebar(open) {
    sidebar.classList.toggle("is-open", open);
    syncScrim();
  }

  function closeDrawers() {
    sidebar.classList.remove("is-open");
    if (window.matchMedia("(max-width: 920px)").matches) review.classList.remove("is-open");
    syncScrim();
  }

  function syncScrim() {
    var narrow = window.matchMedia("(max-width: 920px)").matches;
    var open = narrow && (sidebar.classList.contains("is-open") || (!review.hidden && review.classList.contains("is-open")));
    scrim.hidden = !open;
  }

  function applyTheme(theme) {
    var next = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    themeButton.textContent = next === "light" ? "深色" : "浅色";
    storageSet(window.localStorage, THEME_KEY, next);
  }

  function catalogNotice(data) {
    if (!data) return "";
    if (data.available === false) {
      if (Object.prototype.hasOwnProperty.call(CATALOG_NOTICE, data.notice)) {
        return CATALOG_NOTICE[data.notice];
      }
      return "这次没有取到能力列表。";
    }
    if (data.truncated) return "列表已截断，只显示前面的一部分。";
    if (data.models_notice === "unavailable") return "模型列表暂时取不到。";
    return "";
  }

  function effortWord(id) {
    if (!id) return "推理";
    return EFFORT_LABEL[id] || id;
  }

  function modelWord(id) {
    if (!id) return "模型";
    var model = modelById(id);
    return model ? (model.name || model.id) : id;
  }

  function modelById(id) {
    if (!id || !catalog.data || !Array.isArray(catalog.data.models)) return null;
    for (var i = 0; i < catalog.data.models.length; i += 1) {
      if (catalog.data.models[i] && catalog.data.models[i].id === id) return catalog.data.models[i];
    }
    return null;
  }

  function currentPick() {
    if (catalog.pick) return catalog.pick;
    var data = catalog.data;
    return {
      model: data && typeof data.model === "string" ? data.model : "",
      effort: data && typeof data.reasoning_effort === "string" ? data.reasoning_effort : ""
    };
  }

  function choiceReady() {
    return Boolean(
      selectedSession()
      && state.connected
      && catalog.data
      && catalog.data.available
      && !catalog.loading
      && !catalog.applying
    );
  }

  function setToolLabel(button, text) {
    var label = button.querySelector(".tool-label");
    if (!label) {
      label = document.createElement("span");
      label.className = "tool-label";
      button.replaceChildren(label);
    }
    label.textContent = text;
  }

  function syncChoiceLabels() {
    var pick = currentPick();
    setToolLabel(modelButton, modelWord(pick.model));
    setToolLabel(effortButton, effortWord(pick.effort));
    modelLineLabel.textContent = modelWord(pick.model);
    effortLabelNode.textContent = pick.effort ? effortWord(pick.effort) : "推理";
    effortLabelNode.classList.toggle("is-set", Boolean(pick.effort));
    applyPanelVisibility();
  }

  function applyPanelVisibility() {
    capability.hidden = catalog.panel !== "tools";
    modelMenu.hidden = catalog.panel !== "model";
    modelList.hidden = catalog.panel !== "model" || !catalog.showModels || modelList.childElementCount === 0;
    capabilityToggle.setAttribute("aria-expanded", catalog.panel === "tools" ? "true" : "false");
    modelButton.setAttribute("aria-expanded", catalog.panel === "model" ? "true" : "false");
    effortButton.setAttribute("aria-expanded", catalog.panel === "model" ? "true" : "false");
    modelLine.setAttribute("aria-expanded", catalog.showModels ? "true" : "false");
  }

  function setPanel(next, options) {
    catalog.panel = next || "";
    if (options && Object.prototype.hasOwnProperty.call(options, "showModels")) {
      catalog.showModels = Boolean(options.showModels);
    }
    if (!catalog.panel) catalog.showModels = false;
    if (catalog.panel && window.matchMedia("(max-width: 920px)").matches && !review.hidden) {
      review.hidden = true;
      review.classList.remove("is-open");
      reviewToggle.setAttribute("aria-expanded", "false");
      syncScrim();
    }
    applyPanelVisibility();
    if (catalog.panel && selectedSession() && !catalog.data && !catalog.loading) loadCatalog();
    else paintCapability();
  }

  function syncCatalogSelection() {
    if (catalog.key !== state.selected) {
      catalog.token += 1;
      catalog.applyToken += 1;
      catalog.key = state.selected;
      catalog.data = null;
      catalog.serverModel = "";
      catalog.pick = null;
      catalog.loading = false;
      catalog.applying = false;
      catalog.panel = "";
      catalog.showModels = false;
      catalog.flash = "";
      applyPanelVisibility();
    }
    if (state.selected && selectedSession() && !catalog.data && !catalog.loading && !catalog.applying) {
      loadCatalog();
    }
  }

  function loadCatalog() {
    var session = selectedSession();
    var token = catalog.token + 1;
    catalog.token = token;
    catalog.key = state.selected;
    catalog.flash = "";
    if (!session) {
      catalog.data = null;
      catalog.loading = false;
      paintCapability();
      return;
    }
    catalog.loading = true;
    paintCapability();
    var query = "/api/catalog?agent_id=" + encodeURIComponent(session.agent_id)
      + "&session_id=" + encodeURIComponent(session.session_id)
      + "&project_id=" + encodeURIComponent(session.project_id);
    fetch(query).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        return { ok: response.ok, payload: payload };
      });
    }).catch(function () {
      return { ok: false, payload: { error: "bridge_unavailable" } };
    }).then(function (result) {
      if (token !== catalog.token || catalog.key !== state.selected) return;
      catalog.loading = false;
      if (!result.ok) {
        catalog.data = {
          available: false,
          notice: result.payload.error || "catalog_failed",
          models: [],
          mcp_servers: [],
          skills: []
        };
      } else {
        catalog.data = result.payload;
        catalog.serverModel = typeof result.payload.model === "string" ? result.payload.model : "";
      }
      paintCapability();
    });
  }

  function paintModels() {
    var data = catalog.data || {};
    var pick = currentPick();
    var ready = choiceReady();
    var models = Array.isArray(data.models) ? data.models : [];
    modelList.replaceChildren();
    if (!models.length && catalog.data && catalog.data.available && !catalog.loading) {
      modelList.append(el("p", "hint", data.models_notice === "unavailable" ? "这一项暂时取不到。" : "没有可切换的模型。"));
    }
    models.forEach(function (model) {
      if (!model || typeof model.id !== "string") return;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "menu-item";
      button.setAttribute("role", "option");
      var label = model.name || model.id;
      if (model.is_default) label += " · 默认";
      button.textContent = label;
      button.title = label;
      if (model.id === pick.model) button.setAttribute("aria-current", "true");
      button.disabled = !ready;
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        chooseModel(model.id);
      });
      modelList.append(button);
    });
  }

  function paintEfforts() {
    var pick = currentPick();
    var model = modelById(pick.model);
    var efforts = model && Array.isArray(model.efforts) ? model.efforts : [];
    var ready = choiceReady();
    effortTrack.replaceChildren();
    var fill = document.createElement("span");
    fill.className = "effort-fill";
    fill.setAttribute("aria-hidden", "true");
    effortTrack.append(fill);
    effortTrack.style.setProperty("--fill", "0");
    effortTrack.classList.toggle("is-empty", !efforts.length);
    effortTrack.classList.toggle("is-single", efforts.length === 1);
    if (!model) {
      if (selectedSession() && catalog.data && catalog.data.available && !catalog.loading) {
        effortTrack.append(el("p", "hint", "先选择模型"));
      }
      return;
    }
    if (!efforts.length) {
      effortTrack.append(el("p", "hint", "这个模型没有列出推理强度。"));
      return;
    }
    var index = -1;
    efforts.forEach(function (effort, i) {
      if (effort && effort.id === pick.effort) index = i;
    });
    var amount = efforts.length === 1 ? (index === 0 ? 100 : 0) : (index < 0 ? 0 : (index / (efforts.length - 1)) * 100);
    effortTrack.style.setProperty("--fill", String(amount));
    efforts.forEach(function (effort) {
      if (!effort || typeof effort.id !== "string") return;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "effort-stop";
      button.setAttribute("role", "radio");
      var selected = effort.id === pick.effort;
      var label = effortWord(effort.id);
      if (effort.description) label += " · " + effort.description;
      button.setAttribute("aria-checked", selected ? "true" : "false");
      button.setAttribute("aria-label", label);
      button.title = label;
      button.disabled = !ready;
      button.addEventListener("mouseenter", function () { previewEffort(effort.id); });
      button.addEventListener("focus", function () { previewEffort(effort.id); });
      button.addEventListener("mouseleave", syncChoiceLabels);
      button.addEventListener("blur", syncChoiceLabels);
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        chooseEffort(effort.id);
      });
      effortTrack.append(button);
    });
  }

  function finishSection(list) {
    var heading = list.previousElementSibling;
    var show = list.childElementCount > 0;
    list.hidden = !show;
    if (heading && heading.tagName === "H3") heading.hidden = !show;
  }

  function paintMcp() {
    mcpList.replaceChildren();
    var data = catalog.data;
    if (data && data.mcp_notice === "unavailable") {
      mcpList.append(el("li", "hint", "这一项暂时取不到。"));
    } else if (data) {
      var servers = Array.isArray(data.mcp_servers) ? data.mcp_servers : [];
      if (!servers.length) mcpList.append(el("li", "hint", "没有已列出的 MCP。"));
      servers.forEach(function (server) {
        if (!server || typeof server.name !== "string") return;
        var item = el("li", "catalog-item");
        var name = el("span", "name", server.name);
        name.title = server.name;
        item.append(name);
        item.append(el("span", "meta", MCP_STATUS[server.status] || "未知"));
        mcpList.append(item);
      });
    }
    finishSection(mcpList);
  }

  function paintSkills() {
    skillList.replaceChildren();
    var data = catalog.data;
    if (data && data.skills_notice === "unavailable") {
      skillList.append(el("li", "hint", "这一项暂时取不到。"));
    } else if (data) {
      var skills = Array.isArray(data.skills) ? data.skills : [];
      if (!skills.length) skillList.append(el("li", "hint", "没有已列出的 skills。"));
      skills.forEach(function (skill) {
        if (!skill || typeof skill.name !== "string") return;
        var item = el("li", "catalog-item");
        if (skill.enabled) {
          var button = document.createElement("button");
          button.type = "button";
          button.className = "skill";
          button.title = skill.name;
          button.append(el("span", "skill-name", skill.name));
          button.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            insertSkill(skill.name);
          });
          item.append(button);
        } else {
          var off = el("span", "skill is-off", skill.name);
          off.title = skill.name;
          item.append(off);
        }
        skillList.append(item);
      });
    }
    finishSection(skillList);
  }

  function previewEffort(id) {
    effortLabelNode.textContent = effortWord(id);
    effortLabelNode.classList.add("is-set");
  }

  function paintNotes() {
    var session = selectedSession();
    var line = "";
    if (!session) line = "先选择一个会话";
    else if (catalog.loading) line = "正在读取模型、MCP 和 skills。";
    else line = catalogNotice(catalog.data);
    toolsNote.textContent = line;
    toolsNote.hidden = !line;
    var modelLineText = catalog.flash || line;
    capabilityNote.textContent = modelLineText;
    capabilityNote.hidden = !modelLineText;
  }

  function capabilityWords(session) {
    var names = session && Array.isArray(session.capabilities) ? session.capabilities : [];
    var words = [];
    names.forEach(function (name) {
      if (CAPABILITY_WORD[name] && words.indexOf(CAPABILITY_WORD[name]) < 0) words.push(CAPABILITY_WORD[name]);
    });
    return words.join(" · ");
  }

  function runtimeMeta(session) {
    var parts = [];
    if (session && session.runtime_name) parts.push(session.runtime_name);
    var words = capabilityWords(session);
    if (words) parts.push(words);
    return parts.join(" · ");
  }

  function paintDeclarations() {
    agentCapabilityList.replaceChildren();
    var data = catalog.data;
    var names = data && Array.isArray(data.agent_capabilities) ? data.agent_capabilities : [];
    names.forEach(function (name) {
      if (typeof name !== "string" || !name || /[\s\u0000-\u001f]/.test(name)) return;
      var item = el("li", "catalog-item");
      var label = el("span", "skill is-off", name);
      label.title = name;
      item.append(label);
      agentCapabilityList.append(item);
    });
    finishSection(agentCapabilityList);
  }

  function paintCapability() {
    paintNotes();
    paintModels();
    paintEfforts();
    paintDeclarations();
    paintMcp();
    paintSkills();
    clearChoiceButton.disabled = !choiceReady();
    syncChoiceLabels();
  }

  function insertSkill(name) {
    if (typeof name !== "string" || !/^[^\s]+$/.test(name)) return;
    if (/[\u0000-\u001f]/.test(name)) return;
    var prefix = prompt.value && !/\s$/.test(prompt.value) ? " " : "";
    prompt.value = prompt.value + prefix + "$" + name + " ";
    if (!prompt.disabled) prompt.focus();
    resizePrompt();
  }

  function chooseModel(modelId) {
    var model = modelById(modelId);
    var effort = "";
    var pick = currentPick();
    var ids = [];
    if (model && Array.isArray(model.efforts)) {
      model.efforts.forEach(function (item) {
        if (item && typeof item.id === "string") ids.push(item.id);
      });
    }
    if (pick.effort && ids.indexOf(pick.effort) >= 0) effort = pick.effort;
    else if (model && model.default_effort && ids.indexOf(model.default_effort) >= 0) effort = model.default_effort;
    catalog.showModels = false;
    sendChoice(modelId, effort);
  }

  function chooseEffort(effortId) {
    var pick = currentPick();
    if (!pick.model) {
      catalog.flash = "先选择模型，再选推理强度。";
      paintCapability();
      return;
    }
    sendChoice(pick.model, effortId);
  }

  function sendChoice(modelId, effortId) {
    var session = selectedSession();
    if (!session || !choiceReady()) return;
    if (effortId && !modelId) return;
    catalog.applyToken += 1;
    var applyToken = catalog.applyToken;
    var key = state.selected;
    catalog.applying = true;
    catalog.pick = { model: modelId || "", effort: effortId || "" };
    catalog.flash = "";
    paintCapability();
    control({
      kind: "configure_session",
      agent_id: session.agent_id,
      session_id: session.session_id,
      project_id: session.project_id,
      model: modelId ? modelId : null,
      reasoning_effort: effortId ? effortId : null
    }).then(function (result) {
      if (applyToken !== catalog.applyToken || key !== catalog.key) return;
      catalog.applying = false;
      var code = result.payload && (result.payload.code || result.payload.error);
      if (result.ok && code === "dispatched" && catalog.data && catalog.pick) {
        if (catalog.pick.model) {
          catalog.data.model = catalog.pick.model;
          catalog.data.reasoning_effort = catalog.pick.effort;
        } else {
          catalog.data.model = catalog.serverModel || "";
          catalog.data.reasoning_effort = "";
        }
        catalog.flash = "已记到下一轮。当前这一轮仍用原来的模型。";
      } else if (code === "dispatch_failed") {
        catalog.flash = "请先刷新能力列表，再应用到下一轮。";
      }
      catalog.pick = null;
      announce(describe(result));
      paintCapability();
      render();
    });
  }

  search.addEventListener("input", function () {
    state.query = search.value;
    render();
  });
  composer.addEventListener("submit", submitPrompt);
  prompt.addEventListener("input", resizePrompt);
  prompt.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    composer.requestSubmit();
  });
  focusButton.addEventListener("click", function () { sendSimple("focus"); });
  interruptButton.addEventListener("click", function () { sendSimple("interrupt"); });
  capabilityToggle.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    setPanel(catalog.panel === "tools" ? "" : "tools");
  });
  modelButton.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    if (catalog.panel === "model" && catalog.showModels) setPanel("");
    else setPanel("model", { showModels: true });
  });
  effortButton.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    if (catalog.panel === "model" && !catalog.showModels) setPanel("");
    else setPanel("model", { showModels: false });
  });
  modelLine.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    catalog.showModels = !catalog.showModels;
    applyPanelVisibility();
  });
  clearChoiceButton.addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    sendChoice("", "");
  });
  document.getElementById("close-capability").addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    setPanel("");
  });
  document.getElementById("capability-refresh").addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    loadCatalog();
  });
  document.getElementById("tools-refresh").addEventListener("click", function (event) {
    event.preventDefault();
    event.stopPropagation();
    loadCatalog();
  });
  modelMenu.addEventListener("click", function (event) { event.stopPropagation(); });
  capability.addEventListener("click", function (event) { event.stopPropagation(); });
  document.addEventListener("click", function () {
    if (catalog.panel) setPanel("");
  });
  reviewToggle.addEventListener("click", function () { setReview(review.hidden); });
  document.getElementById("close-review").addEventListener("click", function () { setReview(false); });
  document.getElementById("open-sessions").addEventListener("click", function () {
    setSidebar(!sidebar.classList.contains("is-open"));
  });
  scrim.addEventListener("click", closeDrawers);
  themeButton.addEventListener("click", function () {
    applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  });
  transcript.addEventListener("scroll", function () {
    state.stick = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
  });
  window.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (catalog.panel) {
      setPanel("");
      return;
    }
    closeDrawers();
  });

  applyTheme(storageGet(THEME_KEY) || "dark");
  window.setInterval(tick, 1000);
  fetch("/api/snapshot").then(function (response) { return response.json(); }).then(applySnapshot).catch(function () {
    state.connected = false;
    render();
  });
  connectEvents();
  render();
  paintCapability();
})();
