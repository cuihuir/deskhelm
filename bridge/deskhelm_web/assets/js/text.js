// The page's words. The shared tables arrive with each snapshot from
// deskhelm_client/texts.py, so they have one copy; the page keeps only words
// of its own. The server's message for a command result wins when it sends one.

const words = { result: {}, activity: {}, outcome: {}, failure: {}, resolution: {}, tool: {} };

export function setWords(shared) {
  if (!shared || typeof shared !== "object") return;
  for (const name of Object.keys(words)) {
    if (shared[name] && typeof shared[name] === "object") words[name] = shared[name];
  }
}

// Codes only this page reports.
const PAGE_RESULT_TEXT = {
  request_lost: "页面没有收到回应，结果未知",
  invalid_control_request: "这个操作无法发送",
  too_many_clients: "打开这个页面的客户端太多",
  invalid_catalog_request: "这个请求无法发送",
};

// The command may still have reached the agent.
export const UNKNOWN_OUTCOMES = new Set(["dispatch_failed", "connection_lost", "timeout", "request_lost"]);

export const NOTICE_TEXT = {
  bridge_unavailable: "DeskHelm 没有连上 Bridge，正在重试。",
  bridge_restarted: "Bridge 已重启，之前的活动不会补上。",
  too_many_sessions: "会话太多，有的会话没有显示活动。",
};


const TOOL_ICONS = {
  command: "terminal",
  file_change: "file",
  web_search: "globe",
  mcp: "plug",
};

export function toolWord(kind) {
  return words.tool[kind] || kind || "工具";
}

export function toolIcon(kind) {
  if (TOOL_ICONS[kind]) return TOOL_ICONS[kind];
  const name = String(kind || "").toLowerCase();
  if (/bash|shell|exec|terminal|run/.test(name)) return "terminal";
  if (/edit|write|file|patch|read/.test(name)) return "file";
  if (/search|fetch|web/.test(name)) return "globe";
  return "tool";
}

export const MCP_STATUS = {
  notStarted: "未启动",
  starting: "启动中",
  connected: "已连接",
  authenticationRequired: "需要登录",
  failed: "失败",
  cancelled: "已取消",
  disabled: "已停用",
  unknown: "未知",
};

export const EFFORT_TEXT = {
  minimal: "极低",
  none: "无",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "很高",
  max: "最高",
};

export function resultText(payload, ok) {
  if (payload && typeof payload.message === "string" && payload.message) return payload.message;
  const code = payload && (payload.code || payload.error);
  return words.result[code] || PAGE_RESULT_TEXT[code] || (ok ? "已发送" : "没有完成");
}

const LAST_TURN_TEXT = { completed: "已完成", interrupted: "已中断", failed: "失败" };

export function activityText(session) {
  if (!session) return "";
  if (session.lifecycle === "disconnected") return "已断开";
  // An idle session says how its last turn ended.
  const ended = session.activity === "idle" && session.outcome ? LAST_TURN_TEXT[session.outcome.status] : "";
  return ended || words.activity[session.activity] || session.activity || "";
}

// The state with a failed turn's reason, for places with room to say it.
export function activityDetail(session) {
  const text = activityText(session);
  const outcome = session && session.activity === "idle" ? session.outcome : null;
  const reason = outcome && outcome.status === "failed" ? words.failure[outcome.failure] : "";
  return reason ? `${text}：${reason}` : text;
}

// One tone per state, shared by the sidebar, the header, and the dot.
export function activityTone(session) {
  if (!session || session.lifecycle === "disconnected") return "off";
  if (session.approvals && session.approvals.some((ticket) => ticket.kind === "approval")) return "warn";
  if (session.activity === "waiting_approval" || session.activity === "waiting_input") return "warn";
  if (session.activity === "working" || session.activity === "starting") return "busy";
  if (session.outcome && session.outcome.status === "failed") return "danger";
  return "ok";
}

export function outcomeText(item) {
  const text = words.outcome[item.status] || "";
  const failure = words.failure[item.code] || "";
  return failure ? `${text}：${failure}` : text;
}

// How an approval item ended, as deskhelm_client.texts.resolution_text says it.
export function resolutionText(item, expired) {
  const said = words.resolution;
  if (item.resolution === "deskhelm") {
    const text = said[item.decision === "reject" ? "reject" : "approve"] || "";
    return item.uncertain ? `${text}，${said.uncertain || ""}` : text;
  }
  return said[item.resolution] || (expired ? said.expired || "" : "已结束");
}

export function relativeTime(ms, now = Date.now()) {
  if (!ms) return "";
  const seconds = Math.max(0, Math.round((now - ms) / 1000));
  if (seconds < 45) return "刚刚";
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  const date = new Date(ms);
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

export function countdown(expiresAt, now = Date.now()) {
  const seconds = Math.max(0, Math.ceil((expiresAt - now) / 1000));
  if (seconds <= 0) return "已过期";
  if (seconds < 60) return `${seconds} 秒内有效`;
  return `${Math.ceil(seconds / 60)} 分钟内有效`;
}

export function historyHint(session) {
  if (session && session.agent_id === "codex") return "这里显示这个会话最近的活动。更早的对话仍在 Codex 里。";
  return "这里显示这个会话最近的活动，更早的内容不会保留。";
}

export function capabilityWords(matrix) {
  if (!matrix) return [];
  const words = [];
  const busy = { steer: "工作中可追加指令", queue: "工作中的指令会排队", reject: "工作中不接收指令" };
  if (busy[matrix.prompt_while_busy]) words.push(busy[matrix.prompt_while_busy]);
  if (matrix.interrupt) words.push("可中断");
  const approvals = { answerable: "批准可在这里回答", notify_only: "批准只做提示" };
  if (approvals[matrix.approvals]) words.push(approvals[matrix.approvals]);
  const input = { answerable: "提问可在这里回答", native_ui: "提问在原界面回答" };
  if (input[matrix.user_input]) words.push(input[matrix.user_input]);
  if (matrix.resume) words.push("可恢复");
  if (Array.isArray(matrix.configure) && matrix.configure.length) words.push("可切换模型");
  return words;
}
