from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = REPO_ROOT / "config"
MCP_ENDPOINTS_PATH = CONFIG_DIR / "mcp_endpoints.local.json"
MCP_ACTIONS_PATH = CONFIG_DIR / "mcp_actions.local.json"


def mask_sensitive_data(data: Any) -> Any:
    patterns = ("api_key", "apikey", "token", "secret", "password", "client_secret", "access_key", "local_key")

    def _is_sensitive(key: str) -> bool:
        k = str(key or "").lower()
        return any(p in k for p in patterns)

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                out[k] = "***" if isinstance(k, str) and _is_sensitive(k) else _walk(v)
            return out
        if isinstance(value, list):
            return [_walk(v) for v in value]
        return value

    return _walk(data)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stable_endpoint_id(path: str, methods: list[str], endpoint: str) -> str:
    digest = hashlib.sha1(f"{path}|{','.join(methods)}|{endpoint}".encode("utf-8")).hexdigest()[:12]
    return f"ep_{digest}"


def _flask_rule_to_path_template(path: str) -> str:
    def repl(match: re.Match[str]) -> str:
        name = (match.group(1) or "").split(":", 1)[-1].strip() or "param"
        return "{" + name + "}"

    return re.sub(r"<([^>]+)>", repl, str(path or ""))


def _guess_category(path: str) -> str:
    p = str(path or "")
    if p.startswith("/api/display"):
        return "display"
    if p.startswith("/api/video"):
        return "video"
    if p.startswith("/api/player"):
        return "player"
    if p.startswith("/api/stream"):
        return "stream"
    if p.startswith("/api/media"):
        return "media"
    if p.startswith("/api/portal"):
        return "portal"
    if p.startswith("/api/update"):
        return "system"
    if p.startswith("/api/mcp"):
        return "mcp"
    if p.startswith("/api/"):
        return "system"
    return "unknown"


def _guess_operation(path: str, methods: list[str]) -> str:
    p = str(path or "").lower()
    if "GET" in methods:
        if p.endswith("/status") or p in {"/api/health", "/health"}:
            return "status"
        if p.endswith("/files") or p.endswith("/folders") or p.endswith("/browse"):
            return "list"
        return "read"
    if any(m in methods for m in ["POST", "PUT", "PATCH", "DELETE"]):
        if p.endswith("/sync"):
            return "sync"
        if p.endswith("/play"):
            return "play"
        if p.endswith("/stop"):
            return "stop"
        if "/set" in p or p.endswith("/save"):
            return "set"
        return "write"
    return "unknown"


def _risk_level(path: str, methods: list[str], category: str) -> str:
    p = str(path or "").lower()
    if category in {"portal"}:
        return "dangerous"
    if p.startswith("/api/update"):
        return "dangerous"
    if "/debug/" in p:
        return "dangerous"
    if any(m in methods for m in ["POST", "PUT", "PATCH", "DELETE"]):
        return "high"
    return "low"


def classify_endpoint(rule: Any) -> dict[str, Any]:
    path = str(getattr(rule, "rule", "") or "")
    endpoint_name = str(getattr(rule, "endpoint", "") or "")
    raw_methods = getattr(rule, "methods", None)
    methods = sorted([m for m in (raw_methods or []) if m and m not in {"HEAD", "OPTIONS"}])
    category = _guess_category(path)
    operation = _guess_operation(path, methods)
    risk_level = _risk_level(path, methods, category)
    is_action = any(m in {"POST", "PUT", "PATCH", "DELETE"} for m in methods)
    ignored = endpoint_name == "static" or path.startswith("/static/")
    return {
        "id": _stable_endpoint_id(path, methods, endpoint_name),
        "path": path,
        "path_template": _flask_rule_to_path_template(path),
        "methods": methods,
        "endpoint": endpoint_name,
        "category": category,
        "provider": "display",
        "kind": category,
        "operation": operation,
        "risk_level": risk_level,
        "is_action": is_action,
        "mcp_candidate": (not ignored and risk_level != "dangerous"),
        "ignored": ignored,
        "notes": "",
    }


def discover_flask_endpoints(app: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in getattr(app, "url_map", []).iter_rules():
        item = classify_endpoint(rule)
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        rows.append(item)
    rows.sort(key=lambda x: (str(x.get("path") or ""), ",".join(x.get("methods") or [])))
    return rows


def load_mcp_endpoints() -> list[dict[str, Any]]:
    payload = _read_json(MCP_ENDPOINTS_PATH, {"endpoints": []})
    items = payload.get("endpoints") if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def save_mcp_endpoints(endpoints: list[dict[str, Any]]) -> None:
    _write_json(MCP_ENDPOINTS_PATH, {"generated_at": int(time.time()), "endpoints": endpoints})


def load_mcp_actions() -> list[dict[str, Any]]:
    payload = _read_json(MCP_ACTIONS_PATH, {"actions": []})
    items = payload.get("actions") if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def save_mcp_actions(actions: list[dict[str, Any]]) -> None:
    _write_json(MCP_ACTIONS_PATH, {"generated_at": int(time.time()), "actions": actions})


def _base_action_template() -> dict[str, Any]:
    return {
        "id": "",
        "tool_name": "",
        "display_name": "",
        "description": "",
        "enabled": False,
        "phase": "candidate",
        "provider": "display",
        "capability": "",
        "operation": "",
        "http_method": "GET",
        "endpoint_template": "",
        "source_endpoints": [],
        "required_params": [],
        "optional_params": [],
        "input_schema": {},
        "permission": "DISPLAY_CONTROL",
        "required_role": "ROLE_ADMIN",
        "requires_confirmation": True,
        "dry_run_supported": True,
        "audit_enabled": True,
        "risk_level": "medium",
        "notes": "",
    }


def _merge_existing_action(existing: dict[str, Any] | None, generated: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        return generated
    out = dict(generated)
    for key in {
        "enabled", "phase", "display_name", "description", "permission", "required_role",
        "requires_confirmation", "dry_run_supported", "audit_enabled", "notes", "input_schema",
        "required_params", "optional_params",
    }:
        if key in existing:
            out[key] = existing[key]
    return out


def generate_light_action_candidates(endpoints: list[dict[str, Any]], existing_actions: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    existing_by_id = {str(a.get("id")): a for a in (existing_actions or []) if isinstance(a, dict) and a.get("id")}
    by_path = {str(e.get("path")): str(e.get("id")) for e in endpoints if isinstance(e, dict)}

    def sid(path: str) -> list[str]:
        return [by_path[path]] if path in by_path else []

    defs: list[dict[str, Any]] = []

    for item in [
        ("displayplayer.status", "displayplayer.status", "Display Status", "Display-Status lesen.", "GET", "/api/display/status", "readonly", True, "low", "status", "display.status"),
        ("displayplayer.video.status", "displayplayer.video.status", "Video Status", "Video-Playback-Status lesen.", "GET", "/api/video/status", "readonly", True, "low", "status", "video.status"),
        ("displayplayer.deviceplayer.health", "displayplayer.deviceplayer.health", "Deviceplayer Health", "Deviceplayer-Health lesen.", "GET", "/api/display/deviceplayer/health", "readonly", True, "low", "status", "display.health"),
        ("displayplayer.video.files", "displayplayer.video.files", "Video Files", "Video-Dateien auflisten.", "GET", "/api/video/files", "readonly", True, "low", "list_files", "media.files"),
        ("displayplayer.player.status", "displayplayer.player.status", "Player Service Status", "Player-Service-Status lesen.", "GET", "/api/player/status", "readonly", True, "low", "status", "player.status"),
    ]:
        a = _base_action_template()
        a.update({
            "id": item[0], "tool_name": item[1], "display_name": item[2], "description": item[3],
            "http_method": item[4], "endpoint_template": item[5], "phase": item[6], "enabled": item[7],
            "risk_level": item[8], "operation": item[9], "capability": item[10],
            "requires_confirmation": False, "dry_run_supported": False, "source_endpoints": sid(item[5]),
        })
        defs.append(a)

    cand = [
        ("displayplayer.video.stream.play", "displayplayer.video.stream.play", "Play Stream", "Video-Stream starten.", "/api/video/stream/play", ["url"], "play_stream", "video.play"),
        ("displayplayer.video.file.play", "displayplayer.video.file.play", "Play Video File", "Video-Datei starten.", "/api/video/file/play", ["file_path"], "play_file", "video.play"),
        ("displayplayer.video.stop", "displayplayer.video.stop", "Stop Video", "Video stoppen.", "/api/video/stop", [], "stop", "video.play"),
        ("displayplayer.stream.sync", "displayplayer.stream.sync", "Sync Stream", "Stream-Lokalsync ausführen.", "/api/stream/sync", [], "sync", "stream.sync"),
    ]
    for cid, tname, dname, desc, ep, req, op, cap in cand:
        a = _base_action_template()
        a.update({
            "id": cid,
            "tool_name": tname,
            "display_name": dname,
            "description": desc,
            "enabled": False,
            "phase": "candidate",
            "http_method": "POST",
            "endpoint_template": ep,
            "required_params": req,
            "risk_level": "high",
            "operation": op,
            "capability": cap,
            "source_endpoints": sid(ep),
        })
        defs.append(a)

    out = [_merge_existing_action(existing_by_id.get(str(d.get("id"))), d) for d in defs]
    out.sort(key=lambda x: str(x.get("id") or ""))
    return out


def export_enabled_mcp_tools(actions: list[dict[str, Any]]) -> dict[str, Any]:
    exported: list[dict[str, Any]] = []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        if not bool(a.get("enabled", False)):
            continue
        if str(a.get("phase") or "").strip() != "readonly":
            continue
        if str(a.get("risk_level") or "").strip().lower() == "dangerous":
            continue
        endpoint_template = str(a.get("endpoint_template") or "").strip()
        if endpoint_template.startswith("/api/portal") or endpoint_template.startswith("/api/update") or "/debug/" in endpoint_template:
            continue
        exported.append(
            {
                "id": str(a.get("id") or ""),
                "tool_name": str(a.get("tool_name") or ""),
                "display_name": str(a.get("display_name") or ""),
                "description": str(a.get("description") or ""),
                "provider": str(a.get("provider") or ""),
                "capability": str(a.get("capability") or ""),
                "operation": str(a.get("operation") or ""),
                "http_method": str(a.get("http_method") or ""),
                "endpoint_template": endpoint_template,
                "required_params": a.get("required_params") if isinstance(a.get("required_params"), list) else [],
                "optional_params": a.get("optional_params") if isinstance(a.get("optional_params"), list) else [],
                "input_schema": a.get("input_schema") if isinstance(a.get("input_schema"), dict) else {},
                "permission": str(a.get("permission") or ""),
                "required_role": str(a.get("required_role") or ""),
                "requires_confirmation": bool(a.get("requires_confirmation", True)),
                "dry_run_supported": bool(a.get("dry_run_supported", True)),
                "audit_enabled": bool(a.get("audit_enabled", True)),
                "risk_level": str(a.get("risk_level") or ""),
                "phase": str(a.get("phase") or ""),
                "source_endpoints": a.get("source_endpoints") if isinstance(a.get("source_endpoints"), list) else [],
                "notes": str(a.get("notes") or ""),
            }
        )
    return {"generated_at": int(time.time()), "actions": mask_sensitive_data(exported)}
