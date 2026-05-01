from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from string import Formatter

import requests
from mcp.server.fastmcp import FastMCP

import mcp_registry
from mcp_audit import write_mcp_audit


mcp = FastMCP("joormann-smarthome")

REPO_ROOT = Path(__file__).resolve().parent


def hashlib_sha1(text: str) -> str:
    import hashlib

    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _read_ports_env() -> dict[str, str]:
    config_dir = REPO_ROOT / "config"
    candidates = [
        config_dir / "ports.local.env",
        config_dir / "ports.env",
    ]
    out: dict[str, str] = {}
    for p in candidates:
        if not p.exists():
            continue
        try:
            for raw in p.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
        except Exception:
            continue
    return out


def _smarthome_base_url() -> str:
    # Prefer env, else config/ports*.env, else default 5000 (per request).
    port = str(os.environ.get("FLASK_PORT") or "").strip()
    if not port:
        port = str(_read_ports_env().get("FLASK_PORT") or "").strip()
    if not port.isdigit():
        port = "5000"
    return f"http://127.0.0.1:{int(port)}"


def _safe_format_endpoint(template: str, params: dict[str, Any]) -> str:
    missing: list[str] = []
    required_keys = {k for _, k, _, _ in Formatter().parse(template) if k}
    for key in required_keys:
        if key not in params or params.get(key) is None or str(params.get(key)).strip() == "":
            missing.append(str(key))
    if missing:
        raise ValueError(f"missing_params: {', '.join(sorted(set(missing)))}")
    try:
        return template.format(**params)
    except Exception as exc:
        raise ValueError(f"template_format_failed: {exc}") from exc


def _http_get_json(path_or_url: str, timeout: int = 10) -> dict[str, Any]:
    base = _smarthome_base_url().rstrip("/") + "/"
    url = path_or_url
    if path_or_url.startswith("/"):
        url = urljoin(base, path_or_url.lstrip("/"))
    resp = requests.get(url, timeout=timeout, headers={"Accept": "application/json"})
    try:
        data = resp.json()
    except Exception:
        data = {"raw_text": resp.text}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"http_{resp.status_code}", "data": mcp_registry.mask_sensitive_data(data)}
    return {"ok": True, "data": mcp_registry.mask_sensitive_data(data)}


def _build_signature(required: list[str], optional: list[str]) -> inspect.Signature:
    params: list[inspect.Parameter] = []
    for name in required:
        n = str(name or "").strip()
        if not n:
            continue
        params.append(inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, default=inspect._empty, annotation=Any))
    for name in optional:
        n = str(name or "").strip()
        if not n or any(p.name == n for p in params):
            continue
        params.append(inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Any))
    return inspect.Signature(params)


def _register_readonly_tools() -> tuple[list[str], list[dict[str, Any]]]:
    actions = mcp_registry.load_mcp_actions()

    exported = mcp_registry.export_enabled_mcp_tools(actions)
    exported_actions = exported.get("actions") if isinstance(exported, dict) else []
    exported_actions = exported_actions if isinstance(exported_actions, list) else []

    registered: list[str] = []
    ignored: list[dict[str, Any]] = []

    for action in actions:
        if not isinstance(action, dict):
            continue
        tool_name = str(action.get("tool_name") or "").strip()
        if not tool_name:
            continue

        enabled = bool(action.get("enabled", False))
        phase = str(action.get("phase") or "").strip()
        risk = str(action.get("risk_level") or "").strip().lower()
        method = str(action.get("http_method") or "").strip().upper()

        if not enabled or phase != "readonly" or risk == "dangerous":
            ignored.append({"tool": tool_name, "reason": "not_enabled_or_not_readonly_or_dangerous"})
            continue
        if method != "GET":
            ignored.append({"tool": tool_name, "reason": f"method_not_allowed:{method}"})
            continue

        # Ensure tool is part of the current export (defensive).
        if not any(isinstance(x, dict) and str(x.get("tool_name") or "") == tool_name for x in exported_actions):
            ignored.append({"tool": tool_name, "reason": "not_in_export"})
            continue

        description = str(action.get("description") or "").strip()
        endpoint_template = str(action.get("endpoint_template") or "").strip()
        required_params = action.get("required_params") if isinstance(action.get("required_params"), list) else []
        optional_params = action.get("optional_params") if isinstance(action.get("optional_params"), list) else []

        def _factory(a: dict[str, Any]):
            def tool_impl(**kwargs: Any) -> dict[str, Any]:
                tool = str(a.get("tool_name") or "").strip()
                write_mcp_audit(
                    "mcp_tool_call",
                    {
                        "tool": tool,
                        "params": mcp_registry.mask_sensitive_data(kwargs),
                    },
                )

                if str(a.get("phase") or "") != "readonly":
                    return {"ok": False, "error": "Action not allowed in read-only mode"}
                if str(a.get("http_method") or "").upper() != "GET":
                    return {"ok": False, "error": "Action not allowed in read-only mode"}

                tpl = str(a.get("endpoint_template") or "").strip()
                try:
                    path = _safe_format_endpoint(tpl, kwargs)
                except Exception as exc:
                    return {"ok": False, "error": str(exc)}

                if not path.startswith("/"):
                    return {"ok": False, "error": "invalid_endpoint_template"}
                if path.startswith("/api/credentials") or path.startswith("/api/config") or path.startswith("/api/update") or path.startswith("/api/portal") or "/debug/" in path:
                    return {"ok": False, "error": "Action not allowed in read-only mode"}

                return _http_get_json(path)

            tool_impl.__name__ = f"tool_{hashlib_sha1(tool_name)[:8]}"
            tool_impl.__doc__ = description or f"Read-only tool for {tool_name}"
            tool_impl.__signature__ = _build_signature(required_params, optional_params)
            tool_impl.__annotations__ = {p: Any for p in list(required_params) + list(optional_params)}
            return tool_impl

        fn = _factory(dict(action))

        decorator = None
        try:
            decorator = mcp.tool(name=tool_name, description=description)
        except TypeError:
            decorator = mcp.tool(name=tool_name)
        decorator(fn)
        registered.append(tool_name)

    registered.sort()
    return registered, ignored


REGISTERED_TOOLS, IGNORED_ACTIONS = _register_readonly_tools()


@mcp.tool(name="mcp.registry.status", description="Returns MCP server registry status (registered tools + ignored actions).")
def mcp_registry_status() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "registered_count": len(REGISTERED_TOOLS),
            "registered_tools": REGISTERED_TOOLS,
            "ignored_actions": IGNORED_ACTIONS,
            "base_url": _smarthome_base_url(),
        },
    }


if __name__ == "__main__":
    mcp.run()
