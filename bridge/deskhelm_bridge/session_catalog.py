from __future__ import annotations

from typing import Any


MAX_MODELS = 40
MAX_EFFORTS = 8
MAX_MCP_SERVERS = 24
MAX_TOOL_NAMES = 12
MAX_SKILLS = 80
MAX_AGENT_CAPABILITIES = 32
MAX_TOKEN_CHARS = 128
MAX_LABEL_CHARS = 240

CATALOG_NOTICES = frozenset(
    {
        "",
        "catalog_unavailable",
        "catalog_failed",
        "target_not_found",
        "target_inactive",
    }
)
MCP_STATUSES = frozenset(
    {
        "unknown",
        "notStarted",
        "starting",
        "connected",
        "authenticationRequired",
        "failed",
        "cancelled",
        "disabled",
    }
)
MCP_AUTH = frozenset(
    {
        "",
        "unknown",
        "unsupported",
        "notLoggedIn",
        "bearerToken",
        "oAuth",
    }
)
SKILL_SCOPES = frozenset({"user", "repo", "system", "admin", "unknown"})
SECTION_NOTICES = frozenset({"", "unavailable"})


def empty_catalog(notice: str) -> dict[str, Any]:
    if notice not in CATALOG_NOTICES or notice == "":
        notice = "catalog_failed"
    return {
        "available": False,
        "notice": notice,
        "model": "",
        "reasoning_effort": "",
        "models": [],
        "models_notice": "unavailable",
        "mcp_servers": [],
        "mcp_notice": "unavailable",
        "skills": [],
        "skills_notice": "unavailable",
        "agent_capabilities": [],
        "truncated": False,
    }


def resolve_session_catalog(
    *,
    found: bool,
    active: bool,
    raw: object | None,
    failed: bool,
) -> dict[str, Any]:
    if not found:
        return empty_catalog("target_not_found")
    if not active:
        return empty_catalog("target_inactive")
    if failed:
        return empty_catalog("catalog_failed")
    if raw is None:
        return empty_catalog("catalog_unavailable")
    return normalize_catalog(raw)


def normalize_catalog(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return empty_catalog("catalog_failed")
    notice = value.get("notice", "")
    if notice not in CATALOG_NOTICES:
        return empty_catalog("catalog_failed")
    if notice:
        return empty_catalog(notice)
    models, models_cut = _models(value.get("models"))
    servers, servers_cut = _servers(value.get("mcp_servers"))
    skills, skills_cut = _skills(value.get("skills"))
    capabilities, capabilities_cut = _agent_capabilities(
        value.get("agent_capabilities")
    )
    return {
        "available": True,
        "notice": "",
        "model": _token(value.get("model"), MAX_TOKEN_CHARS),
        "reasoning_effort": _token(value.get("reasoning_effort"), 64),
        "models": models,
        "models_notice": _section_notice(value.get("models_notice")),
        "mcp_servers": servers,
        "mcp_notice": _section_notice(value.get("mcp_notice")),
        "skills": skills,
        "skills_notice": _section_notice(value.get("skills_notice")),
        "agent_capabilities": capabilities,
        "truncated": (
            bool(value.get("truncated"))
            or models_cut
            or servers_cut
            or skills_cut
            or capabilities_cut
        ),
    }


def _models(value: object) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    models: list[dict[str, Any]] = []
    for item in value[:MAX_MODELS]:
        if not isinstance(item, dict):
            continue
        model_id = _token(item.get("id"), MAX_TOKEN_CHARS)
        if not model_id:
            continue
        efforts, _cut = _efforts(item.get("efforts"))
        models.append(
            {
                "id": model_id,
                "name": _label(item.get("name"), MAX_TOKEN_CHARS) or model_id,
                "is_default": item.get("is_default") is True,
                "default_effort": _token(item.get("default_effort"), 64),
                "efforts": efforts,
            }
        )
    return models, len(value) > MAX_MODELS


def _efforts(value: object) -> tuple[list[dict[str, str]], bool]:
    if not isinstance(value, list):
        return [], False
    efforts: list[dict[str, str]] = []
    for item in value[:MAX_EFFORTS]:
        if not isinstance(item, dict):
            continue
        effort_id = _token(item.get("id"), 64)
        if not effort_id:
            continue
        efforts.append(
            {
                "id": effort_id,
                "description": _label(item.get("description"), MAX_LABEL_CHARS),
            }
        )
    return efforts, len(value) > MAX_EFFORTS


def _servers(value: object) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    servers: list[dict[str, Any]] = []
    for item in value[:MAX_MCP_SERVERS]:
        if not isinstance(item, dict):
            continue
        name = _token(item.get("name"), MAX_TOKEN_CHARS)
        if not name:
            continue
        status = item.get("status")
        auth = item.get("auth", "")
        tools, _cut = _tool_names(item.get("tools"))
        servers.append(
            {
                "name": name,
                "status": status if status in MCP_STATUSES else "unknown",
                "auth": auth if auth in MCP_AUTH else "",
                "tools": tools,
            }
        )
    return servers, len(value) > MAX_MCP_SERVERS


def _tool_names(value: object) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    names: list[str] = []
    for item in value[:MAX_TOOL_NAMES]:
        name = _token(item, MAX_TOKEN_CHARS)
        if name and name not in names:
            names.append(name)
    return names, len(value) > MAX_TOOL_NAMES


def _agent_capabilities(value: object) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    names: list[str] = []
    for item in value[:MAX_AGENT_CAPABILITIES]:
        name = _token(item, MAX_TOKEN_CHARS)
        if not name or name in names or "/" in name or "\\" in name or ".." in name:
            continue
        names.append(name)
    return names, len(value) > MAX_AGENT_CAPABILITIES


def _skills(value: object) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    skills: list[dict[str, Any]] = []
    for item in value[:MAX_SKILLS]:
        if not isinstance(item, dict):
            continue
        name = _token(item.get("name"), MAX_TOKEN_CHARS)
        if not name:
            continue
        scope = item.get("scope")
        skills.append(
            {
                "name": name,
                "description": _label(item.get("description"), MAX_LABEL_CHARS),
                "scope": scope if scope in SKILL_SCOPES else "unknown",
                "enabled": item.get("enabled") is True,
            }
        )
    return skills, len(value) > MAX_SKILLS


def _section_notice(value: object) -> str:
    if value in SECTION_NOTICES:
        return str(value)
    return "unavailable"


def _token(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > limit:
        return ""
    if any(character.isspace() or ord(character) < 32 for character in text):
        return ""
    return text


def _label(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
