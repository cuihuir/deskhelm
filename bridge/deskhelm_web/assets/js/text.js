// The page's words. The first four tables mirror deskhelm_client/texts.py,
// which a test keeps in step; the server's own message wins when it sends one.

export const RESULT_TEXT = {
  focused: "已聚焦这个会话",
  dispatched: "已交给 Agent",
  expired: "这个操作已过期",
  issuer_mismatch: "操作来源不匹配",
  invalid_command: "这个操作无法发送",
  idempotency_conflict: "重复的操作冲突",
  idempotency_capacity: "操作记录已满，请稍后再试",
  target_not_found: "找不到这个会话",
  target_inactive: "这个会话不在活动状态",
  not_permitted: "这里不能发出这个操作",
  unsupported: "这个 Agent 不支持这个操作",
  approval_not_found: "找不到这条批准请求",
  approval_already_decided: "这条批准已经处理过",
  undelivered: "Agent 当前无法接收这个操作",
  dispatch_failed: "操作可能没有完成，请查看 Agent",
  bridge_unavailable: "Bridge 暂时不可用",
  connection_lost: "与 Bridge 的连接中断，结果未知",
  timeout: "Bridge 没有及时回应，结果未知",
  client_busy: "操作太多，请稍后再试",
  request_lost: "页面没有收到回应，结果未知",
  invalid_control_request: "这个操作无法发送",
  too_many_clients: "打开这个页面的客户端太多",
  invalid_catalog_request: "这个请求无法发送",
};

export const ACTIVITY_TEXT = {
  starting: "启动中",
  idle: "空闲",
  working: "工作中",
  waiting_approval: "等待批准",
  waiting_input: "等待输入",
};

export const OUTCOME_TEXT = {
  completed: "这一轮已完成",
  interrupted: "这一轮已中断",
  failed: "这一轮失败",
};

export const FAILURE_TEXT = {
  provider: "Agent 报告了错误",
  usage_limit: "用量已到上限",
  auth: "需要重新登录",
  protocol: "Agent 的回应无法识别",
  transport_lost: "与 Agent 的连接中断",
  process_exit: "Agent 进程已退出",
  timeout: "Agent 没有及时回应",
};

// The command may still have reached the agent.
export const UNKNOWN_OUTCOMES = new Set(["dispatch_failed", "connection_lost", "timeout", "request_lost"]);

export const NOTICE_TEXT = {
  bridge_unavailable: "DeskHelm 没有连上 Bridge，正在重试。",
  bridge_restarted: "Bridge 已重启，之前的活动不会补上。",
  too_many_sessions: "会话太多，有的会话没有显示活动。",
};

const TOOL_WORDS = {
  command: "命令",
  file_change: "修改文件",
  web_search: "搜索",
  mcp: "MCP",
  tool: "工具",
};

const TOOL_ICONS = {
  command: "terminal",
  file_change: "file",
  web_search: "globe",
  mcp: "plug",
};

export function toolWord(kind) {
  return TOOL_WORDS[kind] || kind || "工具";
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
  return RESULT_TEXT[code] || (ok ? "已发送" : "没有完成");
}

export function activityText(session) {
  if (!session) return "";
  if (session.lifecycle === "disconnected") return "已断开";
  return ACTIVITY_TEXT[session.activity] || session.activity || "";
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
  const text = OUTCOME_TEXT[item.status] || "";
  const failure = FAILURE_TEXT[item.code] || "";
  return failure ? `${text}：${failure}` : text;
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
