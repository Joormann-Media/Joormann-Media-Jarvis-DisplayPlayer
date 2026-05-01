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
    patterns = (
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "client_secret",
        "access_key",
        "accesskey",
        "local_key",
        "app_key",
    )

    def _mask_key(key: str) -> bool:
        k = (key or "").strip().lower()
        return any(p in k for p in patterns)

    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for k, v in value.items():
                if isinstance(k, str) and _mask_key(k):
                    out[k] = "***"
                else:
                    out[k] = _walk(v)
            return out
        if isinstance(value, list):
            return [_walk(v) for v in value]
        if isinstance(value, tuple):
            return [_walk(v) for v in value]
        return value

    return _walk(data)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stable_endpoint_id(path: str, methods: list[str], endpoint: str) -> str:
    payload = f"{path}|{','.join(methods)}|{endpoint}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"ep_{digest}"


def _flask_rule_to_path_template(path: str) -> str:
    # Flask rules look like: /api/hue/light/<light_id>/on or /x/<int:device_id>
    # Convert to a braces-based template while keeping param names consistent.
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1) or ""
        name = inner.split(":", 1)[-1].strip()
        return "{" + (name or "param") + "}"

    return re.sub(r"<([^>]+)>", repl, str(path or ""))


def _guess_category_provider(path: str) -> tuple[str, str | None]:
    p = (path or "").strip()
    if p.startswith("/api/hue/"):
        return "hue", "hue"
    if p.startswith("/api/avm/"):
        return "avm", "avm"
    if p.startswith("/api/tuya/"):
        return "tuya", "tuya"
    if p.startswith("/api/tapo/"):
        return "tapo", "tapo"
    if p.startswith("/api/lights"):
        return "aggregation", None
    if p.startswith("/api/credentials") or p.startswith("/api/config"):
        return "credentials", None
    if p.startswith("/api/portal"):
        return "portal", None
    if p.startswith("/api/update"):
        return "system", None
    if p.startswith("/api/"):
        return "system", None
    if p in {"/health", "/info"}:
        return "system", None
    if p.startswith("/static/"):
        return "system", None
    return "unknown", None


def _guess_kind(path: str) -> str:
    p = (path or "").lower()
    if "/credentials" in p or p.startswith("/api/config"):
        return "config"
    if "/portal" in p or "/relink" in p or p.endswith("/link"):
        return "portal"
    if "/update" in p:
        return "system"
    if "/manifest" in p or p.endswith("/status") or p.endswith("/health"):
        return "status"
    if "/camera" in p:
        return "camera"
    if "/plug" in p:
        return "switch"
    if "/light" in p or "/lights" in p:
        return "light"
    if "/device" in p:
        return "unknown"
    return "unknown"


def _guess_operation(path: str, methods: list[str]) -> str:
    p = (path or "").lower()
    m = set(methods)
    if "GET" in m:
        if p.endswith("/status") or p in {"/api/status", "/health", "/api/manifest"}:
            return "status"
        if p.endswith("/manifest"):
            return "status"
        if p.endswith("/devices") or p.endswith("/lights") or p.endswith("/lights/all") or p.endswith("/api/lights"):
            return "list"
        if "/light/" in p or "/device/" in p:
            return "get_state"
        return "unknown"
    if any(x in m for x in {"POST", "PUT", "DELETE", "PATCH"}):
        if p.endswith("/on"):
            return "on"
        if p.endswith("/off"):
            return "off"
        if p.endswith("/brightness"):
            return "brightness"
        if p.endswith("/color"):
            return "color"
        if p.endswith("/colortemp"):
            return "colortemp"
        if p.endswith("/state") or p.endswith("/bulk/state"):
            return "state" if p.endswith("/state") else "bulk_state"
        if "/sync" in p or "/fetch" in p:
            return "sync"
        if p.endswith("/config"):
            return "config"
        return "unknown"
    return "unknown"


def _risk_level(path: str, methods: list[str], category: str) -> str:
    p = (path or "").lower()
    m = set(methods)
    if category in {"credentials"}:
        return "dangerous"
    if "/api/update/apply" in p:
        return "dangerous"
    if "/api/portal/" in p:
        return "dangerous"
    if "/api/tapo/debug/" in p:
        return "dangerous"
    if any(x in m for x in {"POST", "PUT", "DELETE", "PATCH"}):
        if "/bulk/" in p:
            return "high"
        if "/tuya/" in p or "/hue/" in p or "/avm/" in p or "/tapo/" in p or "/api/lights/" in p:
            return "high"
        return "medium"
    return "low"


def classify_endpoint(rule: Any) -> dict[str, Any]:
    path = str(getattr(rule, "rule", "") or "")
    endpoint_name = str(getattr(rule, "endpoint", "") or "")
    raw_methods = getattr(rule, "methods", None)
    methods = sorted([m for m in (raw_methods or []) if m and m not in {"HEAD", "OPTIONS"}])
    path_template = _flask_rule_to_path_template(path)

    category, provider = _guess_category_provider(path)
    kind = _guess_kind(path)
    operation = _guess_operation(path, methods)
    risk_level = _risk_level(path, methods, category)

    is_action = any(m in {"POST", "PUT", "DELETE", "PATCH"} for m in methods)
    if operation in {"list", "get_state", "status"}:
        is_action = False

    mcp_candidate = False
    if risk_level != "dangerous":
        if operation in {"list", "get_state", "status"}:
            mcp_candidate = True
        elif kind in {"light", "switch"} and is_action:
            mcp_candidate = True

    ignored = False
    if endpoint_name == "static" or path.startswith("/static/"):
        ignored = True
        mcp_candidate = False

    return {
        "id": _stable_endpoint_id(path, methods, endpoint_name),
        "path": path,
        "path_template": path_template,
        "methods": methods,
        "endpoint": endpoint_name,
        "category": category,
        "provider": provider,
        "kind": kind,
        "operation": operation,
        "risk_level": risk_level,
        "is_action": bool(is_action),
        "mcp_candidate": bool(mcp_candidate),
        "ignored": bool(ignored),
        "notes": "",
    }


def discover_flask_endpoints(app: Any) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in getattr(app, "url_map", []).iter_rules():
        item = classify_endpoint(rule)
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        endpoints.append(item)
    endpoints.sort(key=lambda x: (str(x.get("path") or ""), ",".join(x.get("methods") or [])))
    return endpoints


def load_mcp_endpoints() -> list[dict[str, Any]]:
    data = _read_json(MCP_ENDPOINTS_PATH, default={"endpoints": [], "generated_at": None})
    items = data.get("endpoints") if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def save_mcp_endpoints(endpoints: list[dict[str, Any]]) -> None:
    _write_json(MCP_ENDPOINTS_PATH, {"generated_at": int(time.time()), "endpoints": endpoints})


def load_mcp_actions() -> list[dict[str, Any]]:
    data = _read_json(MCP_ACTIONS_PATH, default={"actions": [], "generated_at": None})
    items = data.get("actions") if isinstance(data, dict) else []
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
        "provider": "aggregation",
        "capability": "",
        "operation": "",
        "http_method": "GET",
        "endpoint_template": "",
        "source_endpoints": [],
        "required_params": [],
        "optional_params": [],
        "input_schema": {},
        "permission": "SMART_HOME_CONTROL",
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
    preserve_fields = {
        "enabled",
        "phase",
        "display_name",
        "description",
        "permission",
        "required_role",
        "requires_confirmation",
        "dry_run_supported",
        "audit_enabled",
        "notes",
        "input_schema",
        "required_params",
        "optional_params",
    }
    for key in preserve_fields:
        if key in existing:
            out[key] = existing[key]
    return out


def generate_light_action_candidates(
    endpoints: list[dict[str, Any]],
    existing_actions: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    existing_by_id: dict[str, dict[str, Any]] = {}
    for a in (existing_actions or []):
        if isinstance(a, dict) and str(a.get("id") or "").strip():
            existing_by_id[str(a["id"])] = a

    endpoints_by_id: dict[str, dict[str, Any]] = {str(e.get("id")): e for e in endpoints if isinstance(e, dict) and e.get("id")}

    def _source_ids_for_paths(paths: list[str]) -> list[str]:
        out: list[str] = []
        path_set = set(paths)
        for eid, e in endpoints_by_id.items():
            if str(e.get("path") or "") in path_set:
                out.append(eid)
        return sorted(set(out))

    generated: list[dict[str, Any]] = []

    # Phase 1: read-only (enabled)
    readonly_defs: list[dict[str, Any]] = []

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.list_lights",
            "tool_name": "smarthome.list_lights",
            "display_name": "List Lights (Provider)",
            "description": "Lists lights for a given provider (hue|avm|tuya).",
            "enabled": True,
            "phase": "readonly",
            "provider": "aggregation",
            "capability": "light.state",
            "operation": "list_devices",
            "http_method": "GET",
            "endpoint_template": "/api/lights?provider={provider}",
            "required_params": ["provider"],
            "optional_params": [],
            "requires_confirmation": False,
            "dry_run_supported": False,
            "risk_level": "low",
            "source_endpoints": _source_ids_for_paths(["/api/lights"]),
        }
    )
    readonly_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.list_all_lights",
            "tool_name": "smarthome.list_all_lights",
            "display_name": "List All Lights",
            "description": "Lists all lights across providers (where supported by the API).",
            "enabled": True,
            "phase": "readonly",
            "provider": "aggregation",
            "capability": "light.state",
            "operation": "list_devices",
            "http_method": "GET",
            "endpoint_template": "/api/lights/all",
            "required_params": [],
            "optional_params": [],
            "requires_confirmation": False,
            "dry_run_supported": False,
            "risk_level": "low",
            "source_endpoints": _source_ids_for_paths(["/api/lights/all"]),
        }
    )
    readonly_defs.append(a)

    # Provider-specific get_state (enabled)
    provider_state_map = [
        ("hue", "/api/hue/light/{device_id}", "light.state"),
        ("avm", "/api/avm/light/{device_id}", "light.state"),
        ("tuya", "/api/tuya/device/{device_id}", "light.state"),
        ("tapo", "/api/tapo/device/{device_id}", "device.state"),
    ]
    for prov, tpl, cap in provider_state_map:
        a = _base_action_template()
        a.update(
            {
                "id": f"smarthome.get_light_state.{prov}",
                "tool_name": "smarthome.get_light_state",
                "display_name": f"Get Device State ({prov.upper()})",
                "description": f"Gets device/light state for provider '{prov}'.",
                "enabled": True,
                "phase": "readonly",
                "provider": prov,
                "capability": cap,
                "operation": "get_state",
                "http_method": "GET",
                "endpoint_template": tpl,
                "required_params": ["device_id"],
                "optional_params": [],
                "requires_confirmation": False,
                "dry_run_supported": False,
                "risk_level": "low",
                "source_endpoints": _source_ids_for_paths([tpl.split("{")[0].rstrip("/") + "<device_id>"]) if "<" in tpl else [],
            }
        )
        # Source endpoint ids: best-effort lookup based on exact paths.
        # For these provider-specific endpoints, we also try matching the real Flask-style templates.
        a["source_endpoints"] = _source_ids_for_paths(
            {
                "hue": ["/api/hue/light/<light_id>", "/api/hue/device/<light_id>"],
                "avm": ["/api/avm/light/<ain>"],
                "tuya": ["/api/tuya/device/<device_id>"],
                "tapo": ["/api/tapo/device/<device_id>"],
            }.get(prov, [])
        )
        readonly_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.provider_status",
            "tool_name": "smarthome.provider_status",
            "display_name": "Provider Status",
            "description": "Returns overall service status (and indirectly provider readiness via capabilities).",
            "enabled": True,
            "phase": "readonly",
            "provider": "system",
            "capability": "system.status",
            "operation": "status",
            "http_method": "GET",
            "endpoint_template": "/api/status",
            "required_params": [],
            "optional_params": [],
            "requires_confirmation": False,
            "dry_run_supported": False,
            "risk_level": "low",
            "source_endpoints": _source_ids_for_paths(["/api/status"]),
        }
    )
    readonly_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.capabilities",
            "tool_name": "smarthome.capabilities",
            "display_name": "Capabilities / Manifest",
            "description": "Returns capabilities and the API catalog/manifest.",
            "enabled": True,
            "phase": "readonly",
            "provider": "system",
            "capability": "system.capabilities",
            "operation": "status",
            "http_method": "GET",
            "endpoint_template": "/api/manifest",
            "required_params": [],
            "optional_params": [],
            "requires_confirmation": False,
            "dry_run_supported": False,
            "risk_level": "low",
            "source_endpoints": _source_ids_for_paths(["/api/manifest"]),
        }
    )
    readonly_defs.append(a)

    for item in readonly_defs:
        merged = _merge_existing_action(existing_by_id.get(str(item["id"])), item)
        generated.append(merged)

    # Phase 1: candidates (disabled by default)
    candidate_defs: list[dict[str, Any]] = []

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.light.switch",
            "tool_name": "smarthome.switch",
            "display_name": "Switch Device On/Off",
            "description": "Switches a light/switch device on or off (candidate; disabled by default).",
            "enabled": False,
            "phase": "candidate",
            "provider": "aggregation",
            "capability": "light.power",
            "operation": "on_off",
            "http_method": "POST",
            "endpoint_template": "/api/lights/{provider}/{device_id}/state",
            "required_params": ["provider", "device_id", "state"],
            "optional_params": [],
            "input_schema": {
                "type": "object",
                "properties": {"state": {"type": "boolean"}},
                "required": ["state"],
            },
            "requires_confirmation": True,
            "dry_run_supported": True,
            "risk_level": "high",
            "source_endpoints": _source_ids_for_paths(["/api/lights/<provider>/<light_id>/state"]),
        }
    )
    candidate_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.light.set_brightness",
            "tool_name": "smarthome.set_brightness",
            "display_name": "Set Brightness",
            "description": "Sets brightness (candidate; disabled by default).",
            "enabled": False,
            "phase": "candidate",
            "provider": "aggregation",
            "capability": "light.brightness",
            "operation": "set_brightness",
            "http_method": "POST",
            "endpoint_template": "/api/lights/{provider}/{device_id}/state",
            "required_params": ["provider", "device_id", "brightness"],
            "optional_params": [],
            "input_schema": {
                "type": "object",
                "properties": {"brightness": {"type": "number", "minimum": 0, "maximum": 100}},
                "required": ["brightness"],
            },
            "requires_confirmation": True,
            "dry_run_supported": True,
            "risk_level": "high",
            "source_endpoints": _source_ids_for_paths(["/api/lights/<provider>/<light_id>/state"]),
        }
    )
    candidate_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.light.set_color",
            "tool_name": "smarthome.set_color",
            "display_name": "Set Color",
            "description": "Sets color as hex (#rrggbb) (candidate; disabled by default).",
            "enabled": False,
            "phase": "candidate",
            "provider": "aggregation",
            "capability": "light.color",
            "operation": "set_color",
            "http_method": "POST",
            "endpoint_template": "/api/lights/{provider}/{device_id}/state",
            "required_params": ["provider", "device_id", "color"],
            "optional_params": [],
            "input_schema": {
                "type": "object",
                "properties": {"color": {"type": "string"}},
                "required": ["color"],
            },
            "requires_confirmation": True,
            "dry_run_supported": True,
            "risk_level": "high",
            "source_endpoints": _source_ids_for_paths(["/api/lights/<provider>/<light_id>/state"]),
        }
    )
    candidate_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.light.set_state",
            "tool_name": "smarthome.set_state",
            "display_name": "Set Light State (Unified)",
            "description": "Sets on/off + brightness + color (candidate; disabled by default).",
            "enabled": False,
            "phase": "candidate",
            "provider": "aggregation",
            "capability": "light.state",
            "operation": "set_state",
            "http_method": "POST",
            "endpoint_template": "/api/lights/{provider}/{device_id}/state",
            "required_params": ["provider", "device_id"],
            "optional_params": ["state", "brightness", "color"],
            "requires_confirmation": True,
            "dry_run_supported": True,
            "risk_level": "high",
            "source_endpoints": _source_ids_for_paths(["/api/lights/<provider>/<light_id>/state"]),
        }
    )
    candidate_defs.append(a)

    # Provider-specific colortemp (candidate; disabled)
    for prov, tpl in [
        ("hue", "/api/hue/light/{device_id}/colortemp"),
        ("avm", "/api/avm/light/{device_id}/colortemp"),
        ("tuya", "/api/tuya/device/{device_id}/colortemp"),
    ]:
        a = _base_action_template()
        a.update(
            {
                "id": f"smarthome.light.set_colortemp.{prov}",
                "tool_name": "smarthome.set_colortemp",
                "display_name": f"Set Color Temperature ({prov.upper()})",
                "description": "Sets color temperature (provider-specific; candidate; disabled by default).",
                "enabled": False,
                "phase": "candidate",
                "provider": prov,
                "capability": "light.colortemp",
                "operation": "set_colortemp",
                "http_method": "PUT",
                "endpoint_template": tpl,
                "required_params": ["device_id", "temp"],
                "optional_params": [],
                "requires_confirmation": True,
                "dry_run_supported": True,
                "risk_level": "high",
                "source_endpoints": _source_ids_for_paths(
                    {
                        "hue": ["/api/hue/light/<light_id>/colortemp"],
                        "avm": ["/api/avm/light/<ain>/colortemp"],
                        "tuya": ["/api/tuya/device/<device_id>/colortemp"],
                    }.get(prov, [])
                ),
            }
        )
        candidate_defs.append(a)

    # Bulk state (candidate; disabled)
    for prov, tpl in [
        ("hue", "/api/hue/lights/bulk/state"),
        ("tuya", "/api/tuya/devices/bulk/state"),
    ]:
        a = _base_action_template()
        a.update(
            {
                "id": f"smarthome.light.bulk_state.{prov}",
                "tool_name": "smarthome.bulk_state",
                "display_name": f"Bulk State ({prov.upper()})",
                "description": "Applies a bulk state update (candidate; disabled by default).",
                "enabled": False,
                "phase": "candidate",
                "provider": prov,
                "capability": "light.state",
                "operation": "bulk_state",
                "http_method": "POST",
                "endpoint_template": tpl,
                "required_params": ["payload"],
                "optional_params": [],
                "requires_confirmation": True,
                "dry_run_supported": True,
                "risk_level": "high",
                "source_endpoints": _source_ids_for_paths([tpl.replace("{device_id}", "<device_id>")]),
            }
        )
        a["source_endpoints"] = _source_ids_for_paths([tpl.replace("{device_id}", "<device_id>")])
        candidate_defs.append(a)

    # Cross-provider multi-target orchestration (candidate; disabled)
    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.actions.execute",
            "tool_name": "smarthome.actions.execute",
            "display_name": "Execute Multi-Target Actions",
            "description": "Executes mixed-provider actions in one request (candidate; disabled by default).",
            "enabled": False,
            "phase": "candidate",
            "provider": "aggregation",
            "capability": "light.state",
            "operation": "multi_execute",
            "http_method": "POST",
            "endpoint_template": "/api/actions/execute",
            "required_params": ["targets"],
            "optional_params": ["parallel", "continue_on_error"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "targets": {"type": "array"},
                    "parallel": {"type": "boolean"},
                    "continue_on_error": {"type": "boolean"},
                },
                "required": ["targets"],
            },
            "requires_confirmation": True,
            "dry_run_supported": True,
            "risk_level": "high",
            "source_endpoints": _source_ids_for_paths(["/api/actions/execute"]),
        }
    )
    candidate_defs.append(a)

    a = _base_action_template()
    a.update(
        {
            "id": "smarthome.actions.status",
            "tool_name": "smarthome.actions.status",
            "display_name": "Read Multi-Target Status",
            "description": "Reads status for mixed-provider targets in one request (candidate; disabled by default).",
            "enabled": False,
            "phase": "candidate",
            "provider": "aggregation",
            "capability": "light.state",
            "operation": "multi_status",
            "http_method": "POST",
            "endpoint_template": "/api/actions/status",
            "required_params": ["targets"],
            "optional_params": ["parallel"],
            "input_schema": {
                "type": "object",
                "properties": {
                    "targets": {"type": "array"},
                    "parallel": {"type": "boolean"},
                },
                "required": ["targets"],
            },
            "requires_confirmation": True,
            "dry_run_supported": True,
            "risk_level": "medium",
            "source_endpoints": _source_ids_for_paths(["/api/actions/status"]),
        }
    )
    candidate_defs.append(a)

    # Tapo plug on/off (candidate; disabled)
    for op, path in [
        ("on", "/api/tapo/device/{device_id}/plug/on"),
        ("off", "/api/tapo/device/{device_id}/plug/off"),
    ]:
        a = _base_action_template()
        a.update(
            {
                "id": f"smarthome.switch.tapo_plug.{op}",
                "tool_name": "smarthome.switch",
                "display_name": f"Tapo Plug {op.upper()}",
                "description": "Switches a Tapo plug locally via python-kasa (candidate; disabled by default).",
                "enabled": False,
                "phase": "candidate",
                "provider": "tapo",
                "capability": "switch.power",
                "operation": "on_off",
                "http_method": "POST",
                "endpoint_template": path,
                "required_params": ["device_id"],
                "optional_params": [],
                "requires_confirmation": True,
                "dry_run_supported": True,
                "risk_level": "high",
                "source_endpoints": _source_ids_for_paths(
                    [
                        "/api/tapo/device/<device_id>/plug/on" if op == "on" else "/api/tapo/device/<device_id>/plug/off"
                    ]
                ),
            }
        )
        candidate_defs.append(a)

    for item in candidate_defs:
        merged = _merge_existing_action(existing_by_id.get(str(item["id"])), item)
        generated.append(merged)

    # Deterministic order
    generated.sort(key=lambda x: str(x.get("id") or ""))
    return generated


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
        if endpoint_template.startswith("/api/credentials") or endpoint_template.startswith("/api/config") or endpoint_template.startswith("/api/update") or endpoint_template.startswith("/api/portal"):
            continue
        if "/debug/" in endpoint_template:
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
