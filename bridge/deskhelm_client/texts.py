"""The one copy of the words surfaces show or speak for Bridge outcomes."""

from __future__ import annotations

from typing import Any

RESULT_TEXT = {
    "focused": "已聚焦这个会话",
    "dispatched": "已交给 Agent",
    "expired": "这个操作已过期",
    "issuer_mismatch": "操作来源不匹配",
    "invalid_command": "这个操作无法发送",
    "idempotency_conflict": "重复的操作冲突",
    "idempotency_capacity": "操作记录已满，请稍后再试",
    "target_not_found": "找不到这个会话",
    "target_inactive": "这个会话不在活动状态",
    "not_permitted": "这里不能发出这个操作",
    "unsupported": "这个 Agent 不支持这个操作",
    "approval_not_found": "找不到这条批准请求",
    "approval_already_decided": "这条批准已经处理过",
    "undelivered": "Agent 当前无法接收这个操作",
    "dispatch_failed": "操作可能没有完成，请查看 Agent",
    # Codes the client itself reports.
    "bridge_unavailable": "Bridge 暂时不可用",
    "connection_lost": "与 Bridge 的连接中断，结果未知",
    "timeout": "Bridge 没有及时回应，结果未知",
    "client_busy": "操作太多，请稍后再试",
}

ACTIVITY_TEXT = {
    "starting": "启动中",
    "idle": "空闲",
    "working": "工作中",
    "waiting_approval": "等待批准",
    "waiting_input": "等待输入",
}

OUTCOME_TEXT = {
    "completed": "这一轮已完成",
    "interrupted": "这一轮已中断",
    "failed": "这一轮失败",
}

FAILURE_TEXT = {
    "provider": "Agent 报告了错误",
    "usage_limit": "用量已到上限",
    "auth": "需要重新登录",
    "protocol": "Agent 的回应无法识别",
    "transport_lost": "与 Agent 的连接中断",
    "process_exit": "Agent 进程已退出",
    "timeout": "Agent 没有及时回应",
}


# How an approval ended; "uncertain" follows a decision whose delivery is unknown.
RESOLUTION_TEXT = {
    "approve": "已批准",
    "reject": "已拒绝",
    "native": "已在 Agent 里处理",
    "cancelled": "已取消",
    "expired": "已过期",
    "uncertain": "结果未知",
}

TOOL_TEXT = {
    "command": "命令",
    "file_change": "修改文件",
    "web_search": "搜索",
    "mcp": "MCP",
    "tool": "工具",
}

_AGENT_LABELS = {"codex": "Codex", "claude": "Claude", "grok": "Grok"}


def result_text(code: str) -> str:
    return RESULT_TEXT.get(code, code)


def activity_text(view: dict[str, Any]) -> str:
    if view.get("lifecycle") == "disconnected":
        return "已断开"
    return ACTIVITY_TEXT.get(str(view.get("activity")), str(view.get("activity") or ""))


def outcome_text(outcome: dict[str, Any] | None) -> str:
    if not outcome:
        return ""
    text = OUTCOME_TEXT.get(str(outcome.get("status")), "")
    failure = FAILURE_TEXT.get(str(outcome.get("failure") or ""))
    return f"{text}：{failure}" if text and failure else text


def tool_text(kind: str) -> str:
    """A tool's fixed kind in words; a vendor's bare tool name stays as it is."""
    return TOOL_TEXT.get(kind, kind or TOOL_TEXT["tool"])


def resolution_text(item: dict[str, Any]) -> str:
    """How a transcript approval item ended, or "" while that is unknown."""
    if item.get("resolution") == "deskhelm":
        text = RESOLUTION_TEXT["reject" if item.get("decision") == "reject" else "approve"]
        return f"{text}，{RESOLUTION_TEXT['uncertain']}" if item.get("uncertain") else text
    return RESOLUTION_TEXT.get(str(item.get("resolution") or ""), "")


def agent_label(agent_id: str, runtime_name: str = "") -> str:
    return _AGENT_LABELS.get(agent_id) or runtime_name or agent_id
