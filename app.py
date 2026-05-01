#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import hashlib
import logging
import platform
import uuid as _uuid_mod
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from flask import Flask, jsonify, render_template, request, stream_with_context, Response, redirect

from services.media_registry import MediaFolderRegistry, MediaFolderValidationError
from services.media_scanner import MediaScanner
from services.media_sync import MediaSyncService
import mcp_audit
import mcp_registry


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_CONFIG_DIR = PROJECT_ROOT / "runtime" / "config"
STREAM_CONFIG_PATH = RUNTIME_CONFIG_DIR / "stream-dashboard.json"
PLAYER_SETUP_CONFIG_PATH = RUNTIME_CONFIG_DIR / "player-service.json"
PORTAL_CONFIG_PATH = RUNTIME_CONFIG_DIR / "portal-link.json"
DISPLAY_CONFIG_PATH = RUNTIME_CONFIG_DIR / "display-config.json"
MEDIA_REGISTRY_CONFIG_PATH = RUNTIME_CONFIG_DIR / "media-registry.json"
DEVICE_PORTAL_MACHINE_ID_PATHS = (
    Path("/home/djanebmb/projects/Joormann-Media-Deviceportal/var/data/device.json"),
    Path("/opt/joormann-media-deviceportal/var/data/device.json"),
)
DEVICE_PORTAL_CONFIG_PATHS = (
    Path("/home/djanebmb/projects/Joormann-Media-Deviceportal/var/data/config.json"),
    Path("/opt/joormann-media-deviceportal/var/data/config.json"),
)
LINUX_MACHINE_ID_PATHS = (
    Path("/etc/machine-id"),
    Path("/var/lib/dbus/machine-id"),
)

DEVICEPLAYER_CONTROL_URL = os.getenv("DEVICEPLAYER_CONTROL_API_HOST", "http://127.0.0.1:5081")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled", "active"}


def make_response(ok: bool, message: str, data: dict[str, Any] | None = None, error_code: str = "", status: int = 200):
    payload = {
        "ok": ok,
        "success": ok,
        "message": message,
        "error_code": error_code,
        "data": data or {},
    }
    return jsonify(payload), status


def _api_err(error_code: str, message: str, status: int = 400):
    return jsonify(ok=False, error=error_code, message=message), status


def _get_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _get_fingerprint() -> tuple[str, str]:
    hostname = socket.gethostname()
    mac_int = _uuid_mod.getnode()
    mac = ":".join(f"{(mac_int >> (8 * i)) & 0xff:02x}" for i in range(5, -1, -1))
    return hashlib.sha256(f"{hostname}:{mac}".encode()).hexdigest(), "v1"


def _get_mac_address() -> str:
    mac_int = _uuid_mod.getnode()
    return ":".join(f"{(mac_int >> (8 * i)) & 0xff:02x}" for i in range(5, -1, -1))


# ---------------------------------------------------------------------------
# HTTP utility
# ---------------------------------------------------------------------------

def _http_post_json(url: str, payload: dict[str, Any], timeout: int = 15) -> tuple[bool, int, dict[str, Any], str]:
    def _preview_text(raw_text: str, limit: int = 220) -> str:
        txt = " ".join(str(raw_text or "").split())
        return txt[:limit]

    def _non_json_payload(status_code: int, raw_text: str, context: str = "") -> dict[str, Any]:
        preview = _preview_text(raw_text)
        lower = preview.lower()
        hint = ""
        if "doctype html" in lower or "<html" in lower:
            hint = "Portal liefert HTML statt JSON."
        message = "Portal-Antwort ist kein gültiges JSON."
        if context:
            message = f"{message} {context}".strip()
        return {
            "ok": False,
            "error": "portal_non_json_response",
            "message": message,
            "detail": {"status": int(status_code), "body_preview": preview, "hint": hint},
        }

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            body_raw = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200))
    except HTTPError as exc:
        try:
            body_raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_raw = ""
        status = int(exc.code or 502)
        try:
            data = json.loads(body_raw) if body_raw else {}
        except Exception:
            data = _non_json_payload(status, body_raw, context=f"(HTTP {status})")
        return False, status, data if isinstance(data, dict) else {}, str(exc)
    except URLError as exc:
        return False, 502, {}, str(exc.reason or exc)
    except Exception as exc:
        return False, 502, {}, str(exc)

    try:
        data = json.loads(body_raw) if body_raw else {}
    except Exception:
        data = _non_json_payload(status, body_raw, context=f"(HTTP {status})")
    return True, status, data if isinstance(data, dict) else {}, ""


def _http_get_json(url: str, timeout: int = 8) -> tuple[bool, dict[str, Any], str]:
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        data = json.loads(body) if body else {}
        return True, data if isinstance(data, dict) else {}, ""
    except Exception as exc:
        return False, {}, str(exc)


# ---------------------------------------------------------------------------
# Systemctl helper
# ---------------------------------------------------------------------------

def _run_systemctl(args: list[str], service_name: str) -> tuple[bool, str, str]:
    if not service_name:
        return False, "", "service_name_missing"
    for cmd_prefix in [[], ["sudo", "-n"]]:
        try:
            proc = subprocess.run(
                [*cmd_prefix, "systemctl", *args, service_name],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                return True, (proc.stdout or "").strip(), ""
        except Exception:
            pass
    try:
        proc = subprocess.run(
            ["systemctl", *args, service_name],
            capture_output=True, text=True, timeout=30, check=False,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        return proc.returncode == 0, out, err
    except Exception as exc:
        return False, "", str(exc)


def _player_service_status(service_name: str) -> dict[str, Any]:
    if not service_name:
        return {"configured": False, "service_name": ""}
    ok, out, err = _run_systemctl(["is-active"], service_name)
    active_state = out.strip() or ("active" if ok else "inactive")
    ok2, out2, _ = _run_systemctl(["is-enabled"], service_name)
    enabled_state = out2.strip() or ("enabled" if ok2 else "disabled")
    return {
        "configured": True,
        "service_name": service_name,
        "active_state": active_state,
        "enabled_state": enabled_state,
        "running": active_state == "active",
        "enabled": enabled_state in {"enabled", "enabled-runtime"},
    }


# ---------------------------------------------------------------------------
# Git update
# ---------------------------------------------------------------------------

def _run_repo_update(mode: str) -> tuple[dict[str, Any], int]:
    git = shutil.which("git")
    if not git:
        return {"ok": False, "error": "git_missing", "message": "git nicht installiert."}, 500
    if mode == "status":
        try:
            proc = subprocess.run([git, "fetch", "--dry-run"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=20)
            proc2 = subprocess.run([git, "log", "HEAD..origin/main", "--oneline"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=10)
            commits_behind = [l for l in (proc2.stdout or "").splitlines() if l.strip()]
            proc3 = subprocess.run([git, "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=5)
            current = (proc3.stdout or "").strip()[:12]
            return {"ok": True, "current_commit": current, "commits_behind": len(commits_behind), "log": commits_behind[:10]}, 200
        except Exception as exc:
            return {"ok": False, "error": str(exc)}, 500
    if mode == "apply":
        try:
            proc = subprocess.run([git, "pull", "--ff-only"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=60)
            ok = proc.returncode == 0
            return {"ok": ok, "stdout": (proc.stdout or "").strip(), "stderr": (proc.stderr or "").strip()}, 200 if ok else 500
        except Exception as exc:
            return {"ok": False, "error": str(exc)}, 500
    return {"ok": False, "error": "unknown_mode"}, 400


# ---------------------------------------------------------------------------
# Configuration: Portal
# ---------------------------------------------------------------------------

def _portal_defaults() -> dict[str, Any]:
    return {
        "url": "",
        "client_id": "",
        "api_key": "",
        "node_uuid": "",
        "node_slug": "",
        "machine_id": "",
        "node_name": "",
        "heartbeat_interval": 60,
    }


def _resolve_machine_id() -> str:
    env_machine_id = str(os.getenv("PORTAL_MACHINE_ID") or os.getenv("JARVIS_MACHINE_ID") or "").strip()
    if env_machine_id:
        return env_machine_id

    override_device_json = str(os.getenv("DEVICE_PORTAL_DEVICE_JSON") or "").strip()
    device_paths = [Path(override_device_json)] if override_device_json else []
    device_paths.extend(DEVICE_PORTAL_MACHINE_ID_PATHS)
    for device_path in device_paths:
        try:
            if not device_path.exists():
                continue
            raw = json.loads(device_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            candidate = str(raw.get("machine_id") or "").strip()
            if candidate:
                return candidate
        except Exception:
            continue

    for machine_id_path in LINUX_MACHINE_ID_PATHS:
        try:
            if not machine_id_path.exists():
                continue
            candidate = machine_id_path.read_text(encoding="utf-8").strip()
            if candidate:
                return candidate
        except Exception:
            continue
    return ""


def _resolve_portal_url() -> str:
    env_portal_url = str(os.getenv("PORTAL_URL") or os.getenv("JARVIS_PORTAL_URL") or "").strip()
    if env_portal_url:
        return env_portal_url.rstrip("/")

    override_device_json = str(os.getenv("DEVICE_PORTAL_CONFIG_JSON") or "").strip()
    config_paths = [Path(override_device_json)] if override_device_json else []
    config_paths.extend(DEVICE_PORTAL_CONFIG_PATHS)
    for config_path in config_paths:
        try:
            if not config_path.exists():
                continue
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            candidate = str(
                raw.get("admin_base_url")
                or raw.get("portal_url")
                or raw.get("base_url")
                or ""
            ).strip()
            if candidate:
                return candidate.rstrip("/")
        except Exception:
            continue
    return ""


def _load_portal_config() -> dict[str, Any]:
    cfg = _portal_defaults()
    if not PORTAL_CONFIG_PATH.exists():
        return cfg
    try:
        data = json.loads(PORTAL_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key in cfg.keys():
        if key in data:
            cfg[key] = data[key]
    cfg["url"] = _resolve_portal_url() or str(cfg.get("url") or "").strip()
    cfg["client_id"] = str(cfg.get("client_id") or "").strip()
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    cfg["node_uuid"] = str(cfg.get("node_uuid") or "").strip()
    cfg["node_slug"] = str(cfg.get("node_slug") or "").strip()
    cfg["machine_id"] = _resolve_machine_id() or str(cfg.get("machine_id") or "").strip()
    cfg["node_name"] = str(cfg.get("node_name") or "").strip()
    try:
        cfg["heartbeat_interval"] = max(10, int(cfg.get("heartbeat_interval") or 60))
    except Exception:
        cfg["heartbeat_interval"] = 60
    return cfg


def _save_portal_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_portal_config()
    if isinstance(data, dict):
        for key in cfg.keys():
            if key in data:
                cfg[key] = data[key]
    cfg["url"] = _resolve_portal_url() or str(cfg.get("url") or "").strip()
    cfg["client_id"] = str(cfg.get("client_id") or "").strip()
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    cfg["node_uuid"] = str(cfg.get("node_uuid") or "").strip()
    cfg["node_slug"] = str(cfg.get("node_slug") or "").strip()
    cfg["machine_id"] = _resolve_machine_id() or str(cfg.get("machine_id") or "").strip()
    cfg["node_name"] = str(cfg.get("node_name") or "").strip()
    try:
        cfg["heartbeat_interval"] = max(10, int(cfg.get("heartbeat_interval") or 60))
    except Exception:
        cfg["heartbeat_interval"] = 60
    RUNTIME_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PORTAL_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _reset_portal_registration(keep_url: str = "", keep_machine_id: str = "", keep_node_name: str = "") -> dict[str, Any]:
    return _save_portal_config({
        "url": str(keep_url or "").strip(),
        "machine_id": str(keep_machine_id or "").strip(),
        "node_name": str(keep_node_name or "").strip(),
        "client_id": "",
        "api_key": "",
        "node_uuid": "",
        "node_slug": "",
    })


# ---------------------------------------------------------------------------
# Configuration: Stream / Player setup / Display
# ---------------------------------------------------------------------------

def _stream_defaults() -> dict[str, Any]:
    return {
        "stream_account_name": "Jarvis-Test",
        "stream_account_key": "jarvis-test-709723",
        "streams_count": 1,
        "admin_base_url": "https://joormann-family.de",
        "selected_stream": "",
        "manifest_version": "",
        "last_sync_at": "",
        "storage_target": "/mnt/deviceportal/media/stream/current",
    }


def _load_stream_config() -> dict[str, Any]:
    cfg = _stream_defaults()
    if not STREAM_CONFIG_PATH.exists():
        return cfg
    try:
        data = json.loads(STREAM_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key in cfg.keys():
        if key in data:
            cfg[key] = data[key]
    try:
        cfg["streams_count"] = max(0, int(cfg.get("streams_count") or 0))
    except Exception:
        cfg["streams_count"] = 0
    return cfg


def _save_stream_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = _stream_defaults()
    current = _load_stream_config()
    cfg.update(current)
    if isinstance(data, dict):
        for key in cfg.keys():
            if key in data:
                cfg[key] = data[key]
    cfg["stream_account_name"] = str(cfg.get("stream_account_name") or "").strip() or "Jarvis-Test"
    cfg["stream_account_key"] = str(cfg.get("stream_account_key") or "").strip() or "jarvis-test-709723"
    cfg["admin_base_url"] = str(cfg.get("admin_base_url") or "").strip() or "https://joormann-family.de"
    cfg["selected_stream"] = str(cfg.get("selected_stream") or "").strip()
    cfg["manifest_version"] = str(cfg.get("manifest_version") or "").strip()
    cfg["last_sync_at"] = str(cfg.get("last_sync_at") or "").strip()
    cfg["storage_target"] = str(cfg.get("storage_target") or "").strip() or "/mnt/deviceportal/media/stream/current"
    try:
        cfg["streams_count"] = max(0, int(cfg.get("streams_count") or 0))
    except Exception:
        cfg["streams_count"] = 0
    RUNTIME_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STREAM_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _player_setup_defaults() -> dict[str, str]:
    default_user = str(os.getenv("USER", "djanebmb") or "djanebmb")
    return {
        "repo_link": "https://github.com/Joormann-Media/Joormann-Media-Jarvis-DisplayPlayer",
        "service_name": "joormann-media-jarvis-displayplayer.service",
        "service_user": default_user,
    }


def _load_player_setup() -> dict[str, str]:
    cfg = _player_setup_defaults()
    if not PLAYER_SETUP_CONFIG_PATH.exists():
        return cfg
    try:
        data = json.loads(PLAYER_SETUP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key in cfg.keys():
        cfg[key] = str(data.get(key) or cfg[key]).strip()
    return cfg


def _save_player_setup(data: dict[str, Any]) -> dict[str, str]:
    cfg = _load_player_setup()
    if isinstance(data, dict):
        for key in cfg.keys():
            if key in data:
                cfg[key] = str(data.get(key) or "").strip()
    defaults = _player_setup_defaults()
    cfg["repo_link"] = cfg.get("repo_link") or defaults["repo_link"]
    cfg["service_name"] = cfg.get("service_name") or defaults["service_name"]
    cfg["service_user"] = cfg.get("service_user") or defaults["service_user"]
    RUNTIME_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PLAYER_SETUP_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def _display_config_defaults() -> dict[str, Any]:
    return {
        "brightness_percent": 100,
        "rotation_degrees": 0,
        "fullscreen": True,
        "resolution_width": 1920,
        "resolution_height": 1080,
        "transition_fps": 30,
        "idle_sleep_ms": 200,
        "video_output": "auto",
        "updated_at": "",
    }


def _load_display_config() -> dict[str, Any]:
    cfg = _display_config_defaults()
    if not DISPLAY_CONFIG_PATH.exists():
        return cfg
    try:
        data = json.loads(DISPLAY_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    for key in cfg.keys():
        if key in data:
            cfg[key] = data[key]
    return cfg


def _save_display_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = _load_display_config()
    if isinstance(data, dict):
        for key in cfg.keys():
            if key in data:
                cfg[key] = data[key]
    try:
        cfg["brightness_percent"] = max(0, min(100, int(float(cfg.get("brightness_percent") or 100))))
    except Exception:
        cfg["brightness_percent"] = 100
    try:
        cfg["rotation_degrees"] = int(cfg.get("rotation_degrees") or 0)
    except Exception:
        cfg["rotation_degrees"] = 0
    cfg["fullscreen"] = bool(cfg.get("fullscreen", True))
    cfg["updated_at"] = utc_now()
    RUNTIME_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DISPLAY_CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# DevicePlayer renderer control (pygame app on port 5081)
# ---------------------------------------------------------------------------

def _deviceplayer_status() -> dict[str, Any]:
    base = DEVICEPLAYER_CONTROL_URL.rstrip("/")
    ok, data, err = _http_get_json(f"{base}/player/status", timeout=4)
    if not ok:
        return {"available": False, "error": err}
    return {"available": True, **data}


def _deviceplayer_health() -> dict[str, Any]:
    base = DEVICEPLAYER_CONTROL_URL.rstrip("/")
    ok, data, err = _http_get_json(f"{base}/health", timeout=4)
    if not ok:
        return {"available": False, "error": err}
    return {"available": True, **data}


def _count_local_assets(storage_target: str) -> int:
    base = Path(str(storage_target or "").strip())
    if not base.exists() or not base.is_dir():
        return 0
    count = 0
    for p in base.rglob("*"):
        if p.is_file():
            count += 1
    return count


# ---------------------------------------------------------------------------
# Video Manager — mpv-based playback for video files and streams
# ---------------------------------------------------------------------------

class VideoManager:
    VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".m4v", ".mpeg", ".mpg", ".wmv", ".flv"}

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._video_proc: subprocess.Popen[str] | None = None
        self._video_url: str | None = None
        self._video_kind: str = "idle"  # idle | file | stream
        self._last_error: str | None = None
        self._started_at: str | None = None
        self._last_update: str = utc_now()

    def _is_running(self, proc: "subprocess.Popen[str] | None") -> bool:
        return proc is not None and proc.poll() is None

    def _mpv_bin(self) -> str:
        return str(os.getenv("DISPLAYPLAYER_MPV_BIN", "mpv") or "mpv").strip() or "mpv"

    def _stop_video_locked(self) -> None:
        proc = self._video_proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        self._video_proc = None

    def _watch_video_proc(self, proc: "subprocess.Popen[str]") -> None:
        stderr_text = ""
        try:
            _, stderr_text = proc.communicate(timeout=3600)
        except Exception:
            pass
        with self._lock:
            if self._video_proc is proc:
                self._video_proc = None
                detail = (stderr_text or "").strip()
                if proc.returncode not in (None, 0) and detail:
                    self._last_error = f"mpv exit {proc.returncode}: {detail[:400]}"
                self._last_update = utc_now()

    def play(self, source: str, kind: str = "file") -> dict[str, Any]:
        source = str(source or "").strip()
        if not source:
            return {"ok": False, "message": "Keine Quelle angegeben.", "error_code": "source_missing"}

        mpv = self._mpv_bin()
        if not shutil.which(mpv):
            return {"ok": False, "message": f"mpv nicht installiert ({mpv}).", "error_code": "mpv_missing"}

        cmd = [mpv, "--really-quiet", "--idle=no", source]

        with self._lock:
            self._stop_video_locked()
            self._last_error = None
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
            except Exception as exc:
                self._last_error = str(exc)
                return {"ok": False, "message": f"Video-Start fehlgeschlagen: {exc}", "error_code": "mpv_start_failed"}

            self._video_proc = proc
            self._video_url = source
            self._video_kind = kind
            self._started_at = utc_now()
            self._last_update = utc_now()
            threading.Thread(target=self._watch_video_proc, args=(proc,), daemon=True).start()
            return {"ok": True, "message": "Video gestartet", "source": source, "kind": kind, "pid": proc.pid}

    def stop(self) -> dict[str, Any]:
        with self._lock:
            was_running = self._is_running(self._video_proc)
            self._stop_video_locked()
            self._video_url = None
            self._video_kind = "idle"
            self._started_at = None
            self._last_update = utc_now()
        return {"ok": True, "message": "Video gestoppt", "was_running": was_running}

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._is_running(self._video_proc)
            return {
                "running": running,
                "source": self._video_url if running else None,
                "kind": self._video_kind if running else "idle",
                "started_at": self._started_at if running else None,
                "last_error": self._last_error,
                "updated_at": self._last_update,
                "pid": self._video_proc.pid if running and self._video_proc else None,
            }

    def list_video_files(self, root: Path, limit: int = 200) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        if not root.exists() or not root.is_dir():
            return files
        try:
            for p in sorted(root.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in self.VIDEO_EXTS:
                    continue
                try:
                    stat = p.stat()
                except Exception:
                    continue
                files.append({
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": int(stat.st_size),
                    "extension": p.suffix.lower().lstrip("."),
                })
                if len(files) >= limit:
                    break
        except Exception:
            pass
        return files


video_manager = VideoManager()

# ---------------------------------------------------------------------------
# Services (lazy init after _http_post_json is defined)
# ---------------------------------------------------------------------------

media_registry = MediaFolderRegistry(MEDIA_REGISTRY_CONFIG_PATH)
media_scanner = MediaScanner(file_limit=5000)
media_sync_service = MediaSyncService(post_json=_http_post_json)


# ---------------------------------------------------------------------------
# Capabilities / API endpoint collection
# ---------------------------------------------------------------------------

def _read_capabilities_doc() -> dict[str, Any]:
    path = PROJECT_ROOT / "capabilities.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _collect_api_endpoints(api_base_url: str) -> list[dict[str, Any]]:
    doc = _read_capabilities_doc()
    sections = doc.get("api_endpoints")
    if not isinstance(sections, list):
        return []
    out: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_name = str(section.get("section") or "API").strip() or "API"
        endpoints = section.get("endpoints")
        if not isinstance(endpoints, list):
            continue
        for ep in endpoints:
            if not isinstance(ep, dict):
                continue
            method = str(ep.get("method") or "GET").strip().upper() or "GET"
            path = str(ep.get("path") or "").strip()
            if not path:
                continue
            if not path.startswith("/"):
                path = f"/{path}"
            out.append({
                "section": section_name,
                "method": method,
                "path": path,
                "url": f"{api_base_url.rstrip('/')}{path}",
                "description": str(ep.get("description") or "").strip(),
            })
    return out


def _node_capabilities() -> dict[str, bool]:
    dp = _deviceplayer_status()
    return {
        "video_output": True,
        "video_playback_control": True,
        "stream_playback": True,
        "display_control": True,
        "media_folder_registry": True,
        "media_folder_scan": True,
        "media_sync": True,
        "deviceplayer_control": dp.get("available", False),
        "manifest_management": True,
    }


def _build_display_payload(
    cfg: dict[str, Any],
    video_status: dict[str, Any],
    api_endpoints: list[dict[str, Any]],
) -> dict[str, Any]:
    endpoint_paths = [str(ep.get("path") or "") for ep in api_endpoints if isinstance(ep, dict)]
    return {
        "deviceRole": "display_player",
        "deviceName": str(cfg.get("node_name") or f"DisplayPlayer ({socket.gethostname()})").strip(),
        "machineId": str(cfg.get("machine_id") or "").strip(),
        "online": True,
        "videoRunning": bool(video_status.get("running")),
        "videoSource": video_status.get("source"),
        "videoKind": video_status.get("kind", "idle"),
        "apiEndpointCount": len(api_endpoints),
        "apiEndpointPaths": endpoint_paths,
        "updatedAt": utc_now(),
    }


def _build_services_payload(
    local_ip: str,
    flask_port: int,
    api_endpoints: list[dict[str, Any]],
    display_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    base_url = f"http://{local_ip}:{flask_port}"
    return [
        {
            "name": "Jarvis DisplayPlayer API",
            "syncId": "jdp-api",
            "serviceType": "api",
            "protocol": "http",
            "host": local_ip,
            "port": flask_port,
            "basePath": "/",
            "baseUrl": base_url,
            "healthcheckPath": "/health",
            "version": "1.0.0",
            "isEnabled": True,
            "isOnline": True,
            "serviceDescription": f"DisplayPlayer API ({len(api_endpoints)} Endpunkte)",
            "endpoints": api_endpoints,
        },
        {
            "name": "Jarvis Display Bridge",
            "syncId": "jdp-display",
            "serviceType": "smarthome_gateway",
            "protocol": "http",
            "host": local_ip,
            "port": flask_port,
            "basePath": "/",
            "baseUrl": base_url,
            "healthcheckPath": "/api/display/status",
            "version": "1.0.0",
            "isEnabled": True,
            "isOnline": True,
            "serviceDescription": "Display Status/Video Bridge",
            "endpoints": api_endpoints,
            "display": display_payload,
        },
    ]


# ---------------------------------------------------------------------------
# Media status payload
# ---------------------------------------------------------------------------

def _media_status_payload() -> dict[str, Any]:
    try:
        folders = media_registry.list_folders()
    except Exception:
        folders = []
    counts_by_status: dict[str, int] = {}
    total_files = 0
    total_media = 0
    total_videos = 0
    total_images = 0
    for item in folders:
        status_key = str(item.get("last_scan_status") or "never").strip() or "never"
        counts_by_status[status_key] = counts_by_status.get(status_key, 0) + 1
        total_files += int(item.get("file_count") or 0)
        total_media += int(item.get("media_count") or 0)
        total_videos += int(item.get("video_count") or 0)
        total_images += int(item.get("image_count") or 0)
    return {
        "folder_count": len(folders),
        "total_file_count": total_files,
        "total_media_count": total_media,
        "total_video_count": total_videos,
        "total_image_count": total_images,
        "scan_status": counts_by_status,
        "folders": [
            {
                "id": str(item.get("id") or ""),
                "path": str(item.get("path") or ""),
                "label": str(item.get("label") or ""),
                "active": bool(item.get("active", True)),
                "file_count": int(item.get("file_count") or 0),
                "media_count": int(item.get("media_count") or 0),
                "video_count": int(item.get("video_count") or 0),
                "image_count": int(item.get("image_count") or 0),
                "last_scan_at": str(item.get("last_scan_at") or ""),
                "last_scan_status": str(item.get("last_scan_status") or ""),
                "last_panel_sync_at": str(item.get("last_panel_sync_at") or ""),
                "last_panel_sync_status": str(item.get("last_panel_sync_status") or ""),
            }
            for item in folders
        ],
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def _health_payload() -> dict[str, Any]:
    video_status = video_manager.status()
    dp = _deviceplayer_health()
    mpv_bin = str(os.getenv("DISPLAYPLAYER_MPV_BIN", "mpv") or "mpv")
    return {
        "service": "joormann-media-jarvis-displayplayer",
        "ok": True,
        "timestamp": utc_now(),
        "version": "1.0.0",
        "checks": {
            "http": "ok",
            "mpv_installed": shutil.which(mpv_bin) is not None,
            "ffmpeg_installed": shutil.which("ffmpeg") is not None,
            "deviceplayer_available": dp.get("available", False),
        },
        "video": {
            "running": video_status.get("running", False),
            "source": video_status.get("source"),
            "kind": video_status.get("kind", "idle"),
        },
        "deviceplayer": dp,
    }


# ---------------------------------------------------------------------------
# Portal registration / sync / heartbeat / relink
# ---------------------------------------------------------------------------

def _portal_register_internal(
    registration_token: str,
    portal_url: str,
    machine_id: str = "",
    node_name: str = "",
) -> tuple[bool, int, dict[str, Any]]:
    cfg = _load_portal_config()
    portal_url = _resolve_portal_url() or str(portal_url or cfg.get("url") or "").strip()
    registration_token = str(registration_token or "").strip()
    machine_id = _resolve_machine_id() or str(machine_id or cfg.get("machine_id") or "").strip()
    node_name = str(node_name or cfg.get("node_name") or "").strip()

    if not portal_url:
        return False, 400, {"ok": False, "error": "portal_url_missing", "message": "Feld 'portal_url' ist erforderlich."}
    if not registration_token:
        return False, 400, {"ok": False, "error": "token_missing", "message": "Feld 'registration_token' ist erforderlich."}

    local_ip = _get_local_ip()
    hostname = socket.gethostname()
    effective_node_name = node_name or f"DisplayPlayer ({hostname})"
    fp_hash, fp_version = _get_fingerprint()
    flask_port = int(os.getenv("FLASK_PORT", "5092"))

    payload: dict[str, Any] = {
        "registrationToken": registration_token,
        "nodeName": effective_node_name,
        "hostname": hostname,
        "type": "displaynode",
        "os": platform.system().lower() or "linux",
        "platform": f"python-flask/{sys.version.split()[0]}",
        "version": "1.0.0",
        "localIp": local_ip,
        "apiBaseUrl": f"http://{local_ip}:{flask_port}",
        "localUrl": f"http://{local_ip}:{flask_port}",
        "fingerprintHash": fp_hash,
        "fingerprintVersion": fp_version,
        "capabilities": _node_capabilities(),
        "description": "Jarvis DisplayPlayer Node — Video, Streams, Slideshow",
        "machineId": machine_id,
    }

    existing_uuid = str(cfg.get("node_uuid") or "").strip()
    if existing_uuid:
        payload["nodeUuid"] = existing_uuid

    ok, status, resp, err = _http_post_json(f"{portal_url.rstrip('/')}/api/jarvis/node/register", payload, timeout=15)
    if not ok and not resp:
        return False, status, {"ok": False, "error": "portal_unreachable", "message": f"Portal nicht erreichbar: {err}"}
    if not resp.get("ok"):
        return False, status, {
            "ok": False,
            "error": "registration_failed",
            "message": str(resp.get("message") or "Registrierung fehlgeschlagen."),
            "detail": resp,
        }

    node_data = ((resp.get("data") or {}).get("node") or {}) if isinstance(resp.get("data"), dict) else {}
    auth_data = ((resp.get("data") or {}).get("auth") or {}) if isinstance(resp.get("data"), dict) else {}
    saved = _save_portal_config({
        "url": portal_url,
        "client_id": str(auth_data.get("clientId") or cfg.get("client_id") or ""),
        "api_key": str(auth_data.get("apiKey") or cfg.get("api_key") or ""),
        "node_uuid": str(node_data.get("uuid") or cfg.get("node_uuid") or ""),
        "node_slug": str(node_data.get("slug") or cfg.get("node_slug") or ""),
        "machine_id": machine_id,
        "node_name": effective_node_name,
    })
    sync_ok, _, sync_data = _do_portal_sync(saved)
    _trigger_async_media_sync_after_link(reason="register")

    return True, (201 if ((resp.get("data") or {}).get("created")) else 200), {
        "ok": True,
        "registered": True,
        "created": bool((resp.get("data") or {}).get("created")),
        "node": node_data,
        "auth": {
            "clientId": saved.get("client_id"),
            "apiKeyPrefix": auth_data.get("apiKeyPrefix"),
            "apiKeyMasked": auth_data.get("apiKeyMasked"),
        },
        "sync": sync_data if sync_ok else {"ok": False, "error": sync_data.get("error"), "message": sync_data.get("message")},
    }


def _do_portal_sync(cfg: dict[str, Any] | None = None) -> tuple[bool, int, dict[str, Any]]:
    cfg = cfg or _load_portal_config()
    portal_url = str(cfg.get("url") or "").strip()
    client_id = str(cfg.get("client_id") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    if not portal_url or not client_id or not api_key:
        return False, 400, {"ok": False, "error": "not_registered", "message": "Portal-Credentials fehlen. POST /api/portal/register zuerst."}

    video_status = video_manager.status()
    local_ip = _get_local_ip()
    flask_port = int(os.getenv("FLASK_PORT", "5092"))
    base_url = f"http://{local_ip}:{flask_port}"
    api_endpoints = _collect_api_endpoints(base_url)
    display_payload = _build_display_payload(cfg, video_status, api_endpoints)
    media_status = _media_status_payload()
    stream_cfg = _load_stream_config()

    payload = {
        "nodeUuid": str(cfg.get("node_uuid") or "").strip(),
        "nodeName": str(cfg.get("node_name") or f"DisplayPlayer ({socket.gethostname()})").strip(),
        "nodeType": "display_player",
        "status": "online",
        "version": "1.0.0",
        "hostname": socket.gethostname(),
        "localIp": local_ip,
        "apiBaseUrl": base_url,
        "localUrl": base_url,
        "platform": f"python-flask/{sys.version.split()[0]}",
        "machineId": str(cfg.get("machine_id") or "").strip(),
        "capabilities": _node_capabilities(),
        "services": _build_services_payload(local_ip, flask_port, api_endpoints, display_payload),
        "syncMeta": {
            "apiEndpoints": api_endpoints,
            "apiEndpointCount": len(api_endpoints),
            "display": display_payload,
            "media": media_status,
            "stream": stream_cfg,
        },
        "statusPayload": {
            "video": video_status,
            "api_endpoints": api_endpoints,
            "display": display_payload,
            "media": media_status,
            "health": _health_payload(),
            "timestamp": utc_now(),
        },
    }
    headers_payload = dict(payload)
    headers_payload["clientId"] = client_id
    headers_payload["apiKey"] = api_key
    ok, status_code, resp, err = _http_post_json(f"{portal_url.rstrip('/')}/api/jarvis/node/sync", headers_payload, timeout=15)
    if not ok and not resp:
        return False, 502, {"ok": False, "error": "portal_unreachable", "message": str(err)}
    if not resp.get("ok"):
        return False, status_code, {"ok": False, "error": "sync_failed", "message": str(resp.get("message") or "Sync fehlgeschlagen"), "detail": resp}
    return True, 200, {"ok": True, "response": resp}


def _do_portal_heartbeat(cfg: dict[str, Any] | None = None) -> tuple[bool, int, dict[str, Any]]:
    cfg = cfg or _load_portal_config()
    portal_url = str(cfg.get("url") or "").strip()
    client_id = str(cfg.get("client_id") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    if not portal_url or not client_id or not api_key:
        return False, 400, {"ok": False, "error": "not_registered", "message": "Portal-Credentials fehlen."}

    video_status = video_manager.status()
    local_ip = _get_local_ip()
    flask_port = int(os.getenv("FLASK_PORT", "5092"))
    base_url = f"http://{local_ip}:{flask_port}"
    api_endpoints = _collect_api_endpoints(base_url)
    display_payload = _build_display_payload(cfg, video_status, api_endpoints)

    payload = {
        "nodeUuid": str(cfg.get("node_uuid") or "").strip(),
        "nodeName": str(cfg.get("node_name") or f"DisplayPlayer ({socket.gethostname()})").strip(),
        "nodeType": "display_player",
        "status": "online",
        "lastSeenAt": utc_now(),
        "lastSyncAt": utc_now(),
        "hostname": socket.gethostname(),
        "localIp": local_ip,
        "apiBaseUrl": base_url,
        "localUrl": base_url,
        "platform": f"python-flask/{sys.version.split()[0]}",
        "version": "1.0.0",
        "machineId": str(cfg.get("machine_id") or "").strip(),
        "capabilities": _node_capabilities(),
        "services": _build_services_payload(local_ip, flask_port, api_endpoints, display_payload),
        "statusPayload": {
            "video": video_status,
            "display": display_payload,
            "health": _health_payload(),
            "timestamp": utc_now(),
        },
    }
    headers_payload = dict(payload)
    headers_payload["clientId"] = client_id
    headers_payload["apiKey"] = api_key
    ok, status_code, resp, err = _http_post_json(f"{portal_url.rstrip('/')}/api/jarvis/node/heartbeat", headers_payload, timeout=12)
    if not ok and not resp:
        return False, 502, {"ok": False, "error": "portal_unreachable", "message": str(err)}
    if not resp.get("ok"):
        return False, status_code, {"ok": False, "error": "heartbeat_failed", "message": str(resp.get("message") or "Heartbeat fehlgeschlagen"), "detail": resp}
    return True, 200, {"ok": True, "response": resp}


# ---------------------------------------------------------------------------
# Media folder scan + sync
# ---------------------------------------------------------------------------

def _scan_and_sync_folder(folder_id: str) -> dict[str, Any]:
    folder = media_registry.get_folder(folder_id)
    if not folder:
        return {"ok": False, "error": "not_found"}

    now = utc_now()
    scan_result = media_scanner.scan_folder(folder["path"])
    summary = scan_result.get("summary", {})
    status = str(summary.get("status") or "error")

    _counts = summary.get("counts_by_type") or {}
    media_registry.update_folder(folder_id, {
        "last_scan_at": now,
        "last_scan_status": status,
        "last_scan_error": str(summary.get("error") or ""),
        "last_scan_summary": summary,
        "last_scan_preview": scan_result.get("files", [])[:20],
        "file_count": int(summary.get("file_count") or 0),
        "media_count": int(summary.get("media_count") or 0),
        "video_count": int(_counts.get("video") or 0),
        "image_count": int(_counts.get("image") or 0),
    })
    folder = media_registry.get_folder(folder_id)

    portal_cfg = _load_portal_config()
    if portal_cfg.get("api_key") and portal_cfg.get("url"):
        flask_port = int(os.getenv("FLASK_PORT", "5092"))
        sync_result = media_sync_service.sync_folder_scan(
            portal_config=portal_cfg,
            folder=folder,
            scan_result=scan_result,
            local_ip=_get_local_ip(),
            flask_port=flask_port,
            timestamp=now,
        )
        sync_status = "ok" if sync_result.get("ok") else "error"
        media_registry.update_folder(folder_id, {
            "last_panel_sync_at": now,
            "last_panel_sync_status": sync_status,
            "last_panel_sync_error": str(sync_result.get("message") or "") if not sync_result.get("ok") else "",
        })

    return {"ok": True, "scan": scan_result}


def _sync_all_active_folders_to_panel(reason: str = "portal_link") -> dict[str, Any]:
    folders = media_registry.list_folders()
    active_folders = [f for f in folders if bool(f.get("active", True))]
    processed = 0
    ok_count = 0
    errors: list[dict[str, str]] = []

    for folder in active_folders:
        folder_id = str(folder.get("id") or "").strip()
        if folder_id == "":
            continue
        processed += 1
        try:
            result = _scan_and_sync_folder(folder_id)
            if bool(result.get("ok")):
                ok_count += 1
            else:
                errors.append({
                    "folder_id": folder_id,
                    "error": str(result.get("error") or "scan_sync_failed"),
                })
        except Exception as exc:
            errors.append({
                "folder_id": folder_id,
                "error": str(exc),
            })

    logger.info(
        "portal media auto-sync finished: reason=%s processed=%s ok=%s errors=%s",
        reason,
        processed,
        ok_count,
        len(errors),
    )
    return {
        "processed": processed,
        "ok": ok_count,
        "errors": errors,
    }


def _trigger_async_media_sync_after_link(reason: str = "portal_link") -> None:
    def _runner() -> None:
        try:
            _sync_all_active_folders_to_panel(reason=reason)
        except Exception as exc:
            logger.warning("portal media auto-sync crashed: reason=%s error=%s", reason, exc)

    thread = threading.Thread(target=_runner, daemon=True, name=f"media-sync-{reason}")
    thread.start()


# ---------------------------------------------------------------------------
# Flask routes — pages
# ---------------------------------------------------------------------------

@app.get("/")
@app.get("/index")
def index():
    return render_template("index.html")


@app.get("/media")
def media():
    folders = media_registry.list_folders()
    return render_template("media.html", folders=folders, media_status=_media_status_payload())


@app.route("/link", methods=["GET", "POST"])
def link_portal():
    cfg = _load_portal_config()
    form = {
        "portal_url": str(cfg.get("url") or "").strip(),
        "registration_token": "",
        "machine_id": str(cfg.get("machine_id") or "").strip(),
        "node_name": str(cfg.get("node_name") or "").strip(),
    }
    result: dict[str, Any] | None = None
    error: str | None = None

    def _format_link_error(payload: dict[str, Any] | None, fallback: str) -> str:
        source = payload if isinstance(payload, dict) else {}
        base_error = str(source.get("message") or source.get("error") or fallback).strip() or fallback
        detail = source.get("detail")
        if not isinstance(detail, dict):
            return base_error
        details: list[str] = []
        if detail.get("status") not in (None, ""):
            details.append(f"HTTP {detail['status']}")
        hint = str(detail.get("hint") or "").strip()
        if hint:
            details.append(hint)
        preview = str(detail.get("body_preview") or "").strip()
        if preview:
            details.append(f"Antwort: {preview}")
        if details:
            return f"{base_error} ({' | '.join(details)})"
        return base_error

    if request.method == "POST":
        action = str(request.form.get("action") or "").strip().lower()
        form["portal_url"] = _resolve_portal_url() or str(request.form.get("portal_url") or "").strip()
        form["registration_token"] = str(request.form.get("registration_token") or "").strip()
        form["machine_id"] = _resolve_machine_id() or str(request.form.get("machine_id") or "").strip()
        form["node_name"] = str(request.form.get("node_name") or "").strip()

        if action == "reset_registration":
            reset_cfg = _reset_portal_registration(
                keep_url=form["portal_url"],
                keep_machine_id=form["machine_id"],
                keep_node_name=form["node_name"],
            )
            form.update({
                "portal_url": str(reset_cfg.get("url") or form["portal_url"]).strip(),
                "machine_id": str(reset_cfg.get("machine_id") or form["machine_id"]).strip(),
                "node_name": str(reset_cfg.get("node_name") or form["node_name"]).strip(),
                "registration_token": "",
            })
            result = {"ok": True, "reset": True}
        elif not form["portal_url"]:
            error = "Portal-URL fehlt."
        else:
            already_registered = bool(cfg.get("client_id") and cfg.get("api_key"))
            if not already_registered and form["registration_token"]:
                ok, _status, payload = _portal_register_internal(
                    registration_token=form["registration_token"],
                    portal_url=form["portal_url"],
                    machine_id=form["machine_id"],
                    node_name=form["node_name"],
                )
                if not ok:
                    error = _format_link_error(payload if isinstance(payload, dict) else None, "Link fehlgeschlagen")
                else:
                    result = payload
                    fresh = _load_portal_config()
                    form["portal_url"] = str(fresh.get("url") or form["portal_url"]).strip()
                    form["machine_id"] = str(fresh.get("machine_id") or form["machine_id"]).strip()
                    form["node_name"] = str(fresh.get("node_name") or form["node_name"]).strip()
                    form["registration_token"] = ""
            else:
                _save_portal_config({
                    "url": form["portal_url"],
                    "machine_id": form["machine_id"],
                    "node_name": form["node_name"],
                })
                if already_registered:
                    sync_ok, _, sync_data = _do_portal_sync()
                    result = {"ok": sync_ok, "updated": True, "sync": sync_data}
                    if sync_ok:
                        _trigger_async_media_sync_after_link(reason="link_save")
                else:
                    error = "Registrierungstoken fehlt. Bitte Token eintragen."

    portal_status = {
        "registered": bool(cfg.get("client_id") and cfg.get("api_key")),
        "portal_url": cfg.get("url") or "",
        "node_uuid": cfg.get("node_uuid") or "",
        "node_slug": cfg.get("node_slug") or "",
        "node_name": cfg.get("node_name") or "",
        "machine_id": cfg.get("machine_id") or "",
        "client_id": cfg.get("client_id") or "",
        "mac_address": _get_mac_address(),
    }
    return render_template("link.html", form=form, result=result, error=error, portal_status=portal_status)


@app.get("/relink")
def relink_portal():
    cfg = _load_portal_config()
    form = {
        "portal_url": str(cfg.get("url") or "").strip(),
        "registration_token": "",
        "machine_id": str(cfg.get("machine_id") or "").strip(),
        "node_name": str(cfg.get("node_name") or "").strip(),
    }
    portal_status = {
        "registered": bool(cfg.get("client_id") and cfg.get("api_key")),
        "portal_url": cfg.get("url") or "",
        "node_uuid": cfg.get("node_uuid") or "",
        "node_slug": cfg.get("node_slug") or "",
        "node_name": cfg.get("node_name") or "",
        "machine_id": cfg.get("machine_id") or "",
        "client_id": cfg.get("client_id") or "",
        "mac_address": _get_mac_address(),
    }
    return render_template("link.html", form=form, result=None, error=None, portal_status=portal_status)


@app.get("/info")
def info():
    caps_path = PROJECT_ROOT / "capabilities.json"
    try:
        caps = json.loads(caps_path.read_text(encoding="utf-8"))
    except Exception:
        caps = {}
    flask_port = int(os.getenv("FLASK_PORT", "5092"))
    api_sections = caps.get("api_endpoints") if isinstance(caps.get("api_endpoints"), list) else []
    endpoint_count = 0
    method_counts: dict[str, int] = {}
    for section in api_sections:
        endpoints = section.get("endpoints") if isinstance(section, dict) and isinstance(section.get("endpoints"), list) else []
        endpoint_count += len(endpoints)
        for ep in endpoints:
            method = str(ep.get("method") or "").upper()
            if method:
                method_counts[method] = method_counts.get(method, 0) + 1
    return render_template(
        "info.html",
        caps=caps,
        port=flask_port,
        endpoint_count=endpoint_count,
        method_counts=method_counts,
        generated_at=utc_now(),
        auth_required=False,
    )


# ---------------------------------------------------------------------------
# Flask routes — health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return jsonify(_health_payload())


@app.get("/api/health")
def api_health():
    return make_response(True, "ok", _health_payload(), "", 200)


# ---------------------------------------------------------------------------
# Flask routes — display / video
# ---------------------------------------------------------------------------

@app.get("/api/display/status")
def api_display_status():
    video_status = video_manager.status()
    dp = _deviceplayer_status()
    display_cfg = _load_display_config()
    return make_response(True, "ok", {
        "video": video_status,
        "deviceplayer": dp,
        "display_config": display_cfg,
        "updated_at": utc_now(),
    }, "", 200)


@app.get("/api/display/config")
def api_display_config_get():
    return make_response(True, "ok", _load_display_config(), "", 200)


@app.post("/api/display/config")
def api_display_config_set():
    body = request.get_json(silent=True) or {}
    saved = _save_display_config(body)
    return make_response(True, "Display-Konfiguration gespeichert.", saved, "", 200)


@app.get("/api/display/deviceplayer/status")
def api_deviceplayer_status():
    return make_response(True, "ok", _deviceplayer_status(), "", 200)


@app.get("/api/display/deviceplayer/health")
def api_deviceplayer_health():
    return make_response(True, "ok", _deviceplayer_health(), "", 200)


# ---------------------------------------------------------------------------
# Flask routes — video playback (mpv)
# ---------------------------------------------------------------------------

@app.get("/api/video/status")
def api_video_status():
    return make_response(True, "ok", video_manager.status(), "", 200)


@app.post("/api/video/stream/play")
def api_video_stream_play():
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or body.get("stream_url") or "").strip()
    if not url:
        return make_response(False, "url fehlt.", {}, "invalid_payload", 400)
    result = video_manager.play(url, kind="stream")
    if not result.get("ok"):
        return make_response(False, result.get("message", "Start fehlgeschlagen"), result, result.get("error_code", "video_start_failed"), 400)
    return make_response(True, "Stream gestartet.", result, "", 200)


@app.post("/api/video/file/play")
def api_video_file_play():
    body = request.get_json(silent=True) or {}
    file_path = str(body.get("file_path") or body.get("path") or "").strip()
    if not file_path:
        return make_response(False, "file_path fehlt.", {}, "invalid_payload", 400)
    p = Path(file_path)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    if not p.exists():
        return make_response(False, f"Datei nicht gefunden: {p}", {}, "file_missing", 404)
    result = video_manager.play(str(p), kind="file")
    if not result.get("ok"):
        return make_response(False, result.get("message", "Start fehlgeschlagen"), result, result.get("error_code", "video_start_failed"), 400)
    return make_response(True, "Video-Datei gestartet.", result, "", 200)


@app.post("/api/video/stop")
def api_video_stop():
    result = video_manager.stop()
    return make_response(True, "Video gestoppt.", result, "", 200)


@app.get("/api/video/files")
def api_video_files():
    stream_cfg = _load_stream_config()
    video_root = Path(str(stream_cfg.get("storage_target") or "/mnt/deviceportal/media/stream/current")) / "video"
    files = video_manager.list_video_files(video_root)
    return make_response(True, "ok", {
        "video_root": str(video_root),
        "count": len(files),
        "files": files,
    }, "", 200)


# ---------------------------------------------------------------------------
# Flask routes — stream / manifest config
# ---------------------------------------------------------------------------

@app.get("/api/stream/config")
def api_stream_config_get():
    cfg = _load_stream_config()
    assets_count = _count_local_assets(str(cfg.get("storage_target") or ""))
    return make_response(True, "ok", {**cfg, "assets_count": assets_count}, "", 200)


@app.post("/api/stream/config")
def api_stream_config_set():
    body = request.get_json(silent=True) or {}
    saved = _save_stream_config(body if isinstance(body, dict) else {})
    assets_count = _count_local_assets(str(saved.get("storage_target") or ""))
    return make_response(True, "Stream-Konfiguration gespeichert.", {**saved, "assets_count": assets_count}, "", 200)


@app.post("/api/stream/sync")
def api_stream_sync():
    cfg = _load_stream_config()
    cfg["last_sync_at"] = utc_now()
    saved = _save_stream_config(cfg)
    assets_count = _count_local_assets(str(saved.get("storage_target") or ""))
    return make_response(True, "Lokaler Sync aktualisiert.", {**saved, "assets_count": assets_count}, "", 200)


# ---------------------------------------------------------------------------
# Flask routes — player service
# ---------------------------------------------------------------------------

@app.get("/api/player/setup")
def api_player_setup_get():
    return make_response(True, "ok", _load_player_setup(), "", 200)


@app.post("/api/player/setup")
def api_player_setup_set():
    body = request.get_json(silent=True) or {}
    saved = _save_player_setup(body if isinstance(body, dict) else {})
    return make_response(True, "Player-Setup gespeichert.", saved, "", 200)


@app.get("/api/player/status")
def api_player_status_get():
    setup = _load_player_setup()
    service_name = str(setup.get("service_name") or "").strip()
    status = _player_service_status(service_name)
    return make_response(True, "ok", {"setup": setup, "service": status}, "", 200)


@app.post("/api/player/service/<action>")
def api_player_service_action(action: str):
    safe_action = str(action or "").strip().lower()
    if safe_action not in {"start", "stop", "restart", "enable", "disable"}:
        return make_response(False, "Ungültige Action.", {}, "invalid_action", 400)
    setup = _load_player_setup()
    service_name = str(setup.get("service_name") or "").strip()
    if not service_name:
        return make_response(False, "Kein Service konfiguriert.", {}, "service_name_missing", 400)
    ok, _out, err = _run_systemctl([safe_action], service_name)
    if not ok:
        return make_response(False, f"Service {safe_action} fehlgeschlagen.", {"detail": err}, "service_action_failed", 500)
    status = _player_service_status(service_name)
    return make_response(True, f"Service {safe_action}.", {"setup": setup, "service": status}, "", 200)


# ---------------------------------------------------------------------------
# Flask routes — dashboard
# ---------------------------------------------------------------------------

@app.get("/api/dashboard/overview")
def api_dashboard_overview():
    stream_cfg = _load_stream_config()
    player_setup = _load_player_setup()
    player_status = _player_service_status(str(player_setup.get("service_name") or ""))
    video_status = video_manager.status()
    dp = _deviceplayer_status()
    display_cfg = _load_display_config()
    media_status = _media_status_payload()
    return make_response(True, "ok", {
        "stream": {**stream_cfg, "assets_count": _count_local_assets(str(stream_cfg.get("storage_target") or ""))},
        "player": {"setup": player_setup, "service": player_status},
        "video": video_status,
        "deviceplayer": dp,
        "display_config": display_cfg,
        "media": media_status,
        "updated_at": utc_now(),
    }, "", 200)


def _parse_port_from_url(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = urlparse(text if "://" in text else f"http://{text}")
        if parsed.port:
            return int(parsed.port)
    except Exception:
        return None
    return None


def _host_matches_local(host_value: str) -> bool:
    host = str(host_value or "").strip().lower()
    if not host:
        return True
    local = socket.gethostname().strip().lower()
    local_short = local.split(".", 1)[0]
    host_short = host.split(".", 1)[0]
    return host in {local, local_short} or host_short in {local, local_short}


def _service_label_from_entry(service_name: str, fallback: str = "") -> str:
    s = str(service_name or "").strip().lower()
    if "audioplayer" in s:
        return "AudioPlayer"
    if "displayplayer" in s:
        return "DisplayPlayer"
    if "smarthome" in s:
        return "Smarthome-Lab"
    if "llm-lab" in s:
        return "LLM-Lab"
    if "tts-lab" in s:
        return "TTS-Lab"
    if "audio-lab" in s:
        return "Audio-Lab"
    return str(fallback or service_name or "Module").strip()


def _local_peer_modules() -> list[dict[str, Any]]:
    current_host = socket.gethostname().strip().lower()
    out: list[dict[str, Any]] = []
    seen_ports: set[int] = set()
    config_paths: list[Path] = []
    override = str(os.getenv("DEVICE_PORTAL_CONFIG_JSON") or "").strip()
    if override:
        config_paths.append(Path(override))
    config_paths.extend(DEVICE_PORTAL_CONFIG_PATHS)

    for path in config_paths:
        try:
            if not path.exists():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        rows = raw.get("autodiscover_services")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            host = str(row.get("hostname") or row.get("node_name") or "").strip().lower()
            if host and not _host_matches_local(host):
                continue
            service_name = str(row.get("service_name") or row.get("name") or "").strip()
            port = None
            for candidate in (
                row.get("port"),
                row.get("flask_port"),
                row.get("service_port"),
                row.get("baseUrl"),
                row.get("localUrl"),
                row.get("apiBaseUrl"),
                row.get("api_base_url"),
                row.get("ui_url"),
                row.get("url"),
                ((row.get("endpoints") or {}).get("api_base") if isinstance(row.get("endpoints"), dict) else None),
                ((row.get("endpoints") or {}).get("ui") if isinstance(row.get("endpoints"), dict) else None),
            ):
                if isinstance(candidate, (int, float)):
                    port = int(candidate)
                    break
                parsed_port = _parse_port_from_url(str(candidate or ""))
                if parsed_port:
                    port = parsed_port
                    break
            if not port or port <= 0:
                continue
            if port in seen_ports:
                continue
            seen_ports.add(port)
            out.append(
                {
                    "name": _service_label_from_entry(service_name, str(row.get("name") or "").strip()),
                    "serviceName": service_name,
                    "port": port,
                    "source": "deviceportal-autodiscover",
                }
            )

    current_port = int(os.getenv("FLASK_PORT", "5092"))
    if current_port not in seen_ports:
        out.append(
            {
                "name": "DisplayPlayer",
                "serviceName": "joormann-media-jarvis-displayplayer.service",
                "port": current_port,
                "source": "self",
            }
        )

    out.sort(key=lambda item: (int(item.get("port") or 0), str(item.get("name") or "").lower()))
    return out


# ---------------------------------------------------------------------------
# Flask routes — media folder management
# ---------------------------------------------------------------------------

@app.get("/api/media/folders")
def api_media_folders_list():
    folders = media_registry.list_folders()
    return make_response(True, "OK", {"folders": folders, "status": _media_status_payload()})


@app.post("/api/media/folders")
def api_media_folders_add():
    body = request.get_json(silent=True) or {}
    path = str(body.get("path") or "").strip()
    label = str(body.get("label") or "").strip()
    try:
        folder = media_registry.add_folder(path, label)
    except MediaFolderValidationError as exc:
        return make_response(False, str(exc), {}, error_code="validation_error", status=400)
    except Exception as exc:
        return make_response(False, str(exc), {}, error_code="add_failed", status=500)
    _scan_and_sync_folder(folder["id"])
    folders = media_registry.list_folders()
    return make_response(True, "Ordner hinzugefügt.", {"folders": folders})


@app.get("/api/media/folders/<folder_id>")
def api_media_folder_detail(folder_id: str):
    folder = media_registry.get_folder(folder_id)
    if not folder:
        return make_response(False, "Ordner nicht gefunden.", {}, error_code="not_found", status=404)
    return make_response(True, "OK", {"folder": folder})


@app.post("/api/media/folders/<folder_id>/scan")
def api_media_folder_scan(folder_id: str):
    folder = media_registry.get_folder(folder_id)
    if not folder:
        return make_response(False, "Ordner nicht gefunden.", {}, error_code="not_found", status=404)
    _scan_and_sync_folder(folder_id)
    folder = media_registry.get_folder(folder_id)
    return make_response(True, "Scan abgeschlossen.", {"folder": folder})


@app.post("/api/media/folders/<folder_id>/remove")
def api_media_folder_remove(folder_id: str):
    removed = media_registry.remove_folder(folder_id)
    if not removed:
        return make_response(False, "Ordner nicht gefunden.", {}, error_code="not_found", status=404)
    return make_response(True, "Ordner entfernt.")


@app.post("/api/media/folders/<folder_id>/active")
def api_media_folder_active(folder_id: str):
    body = request.get_json(silent=True) or {}
    active = bool(body.get("active", True))
    updated = media_registry.update_folder(folder_id, {"active": active})
    if not updated:
        return make_response(False, "Ordner nicht gefunden.", {}, error_code="not_found", status=404)
    return make_response(True, f"Ordner ist jetzt {'aktiv' if active else 'inaktiv'}.", {"folder": updated})


@app.get("/api/media/browse")
def api_media_browse():
    raw_path = request.args.get("path", "/").strip() or "/"
    try:
        target = Path(raw_path).resolve()
    except Exception:
        return make_response(False, "Ungültiger Pfad.", {}, error_code="invalid_path", status=400)
    if not target.exists() or not target.is_dir():
        return make_response(False, "Pfad nicht gefunden.", {}, error_code="not_found", status=404)

    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".m4v", ".mpeg", ".mpg", ".wmv", ".flv"}
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tiff", ".tif", ".avif"}
    dirs: list[dict[str, Any]] = []
    video_files: list[dict[str, Any]] = []
    image_files: list[dict[str, Any]] = []

    try:
        raw_entries = list(target.iterdir())
    except PermissionError:
        return make_response(False, "Zugriff verweigert.", {}, error_code="permission_denied", status=403)
    except OSError as exc:
        return make_response(False, f"Verzeichnis nicht lesbar: {exc}", {}, error_code="os_error", status=500)

    def _safe_is_dir(p: Path) -> bool:
        try:
            return p.is_dir()
        except OSError:
            return False

    try:
        entries = sorted(raw_entries, key=lambda e: (not _safe_is_dir(e), e.name.lower()))
    except Exception:
        entries = raw_entries

    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            is_dir = entry.is_dir()
            is_sym = entry.is_symlink()
        except OSError:
            continue
        try:
            if is_dir and not is_sym:
                try:
                    children = list(entry.iterdir())
                    video_count = sum(1 for f in children if f.is_file() and f.suffix.lower() in video_exts)
                    image_count = sum(1 for f in children if f.is_file() and f.suffix.lower() in image_exts)
                except OSError:
                    video_count = -1
                    image_count = -1
                dirs.append({"name": entry.name, "path": str(entry), "video_count": video_count, "image_count": image_count})
            elif not is_dir:
                ext = entry.suffix.lower()
                stat = entry.stat()
                item = {"name": entry.name, "path": str(entry), "size_bytes": int(stat.st_size), "extension": ext.lstrip(".")}
                if ext in video_exts:
                    video_files.append(item)
                elif ext in image_exts:
                    image_files.append(item)
        except OSError:
            continue

    parts = target.parts
    breadcrumb = [{"label": part if i > 0 else "/", "path": str(Path(*parts[: i + 1])) if i > 0 else "/"} for i, part in enumerate(parts)]
    parent = str(target.parent) if str(target) != "/" else None

    return make_response(True, "OK", {
        "path": str(target),
        "parent": parent,
        "breadcrumb": breadcrumb,
        "dirs": dirs,
        "video_files": video_files,
        "image_files": image_files,
    })


# ---------------------------------------------------------------------------
# Flask routes — portal
# ---------------------------------------------------------------------------

@app.get("/api/portal/status")
def api_portal_status():
    cfg = _load_portal_config()
    return make_response(True, "ok", {
        "registered": bool(cfg.get("client_id") and cfg.get("api_key")),
        "portalUrl": cfg.get("url") or "",
        "nodeUuid": cfg.get("node_uuid") or "",
        "nodeSlug": cfg.get("node_slug") or "",
        "machineId": cfg.get("machine_id") or "",
        "nodeName": cfg.get("node_name") or "",
        "clientId": cfg.get("client_id") or "",
        "apiKeyMasked": ("***" + str(cfg.get("api_key") or "")[-4:]) if cfg.get("api_key") else None,
        "heartbeatInterval": int(cfg.get("heartbeat_interval") or 60),
    }, "", 200)


@app.get("/api/portal/peers")
def api_portal_peers():
    peers = _local_peer_modules()
    return make_response(True, "ok", {"peers": peers}, "", 200)


@app.post("/api/portal/register")
def api_portal_register():
    body = request.get_json(silent=True) or {}
    portal_url = str(body.get("portal_url") or "").strip()
    registration_token = str(body.get("registration_token") or body.get("token") or "").strip()
    machine_id = str(body.get("machine_id") or body.get("machineId") or "").strip()
    node_name = str(body.get("node_name") or "").strip()
    ok, status, payload = _portal_register_internal(
        registration_token=registration_token,
        portal_url=portal_url,
        machine_id=machine_id,
        node_name=node_name,
    )
    return jsonify(payload), status


@app.post("/api/portal/relink")
def api_portal_relink():
    body = request.get_json(silent=True) or {}
    portal_url = str(body.get("portal_url") or "").strip()
    uuid_value = str(body.get("uuid") or body.get("node_uuid") or "").strip()
    slug_value = str(body.get("slug") or body.get("node_slug") or "").strip()
    client_id = str(body.get("client_id") or body.get("clientId") or "").strip()
    mac_address = str(body.get("mac_address") or body.get("macAddress") or "").strip()

    if not portal_url:
        return _api_err("portal_url_missing", "Feld 'portal_url' ist erforderlich.", 400)
    if not uuid_value or not slug_value or not client_id or not mac_address:
        return _api_err("relink_fields_missing", "Felder 'uuid', 'slug', 'client_id' und 'mac_address' sind erforderlich.", 400)

    payload = {"uuid": uuid_value, "slug": slug_value, "clientId": client_id, "macAddress": mac_address}
    ok_relink, relink_status_code, relink_data, relink_err = _http_post_json(
        f"{portal_url.rstrip('/')}/api/jarvis/node/relink", payload, timeout=15,
    )
    if not ok_relink and not relink_data:
        return _api_err("relink_failed", f"Re-Link fehlgeschlagen: {relink_err or 'Unbekannter Fehler'}", 502)
    if not bool(relink_data.get("ok")):
        return jsonify(ok=False, error="relink_failed", message=str(relink_data.get("message") or "Re-Link fehlgeschlagen."), detail=relink_data), int(relink_status_code or 502)

    relink_root = relink_data.get("data") if isinstance(relink_data.get("data"), dict) else {}
    node_data = relink_root.get("node") if isinstance(relink_root.get("node"), dict) else {}
    auth_data = relink_root.get("auth") if isinstance(relink_root.get("auth"), dict) else {}

    portal_cfg = _load_portal_config()
    portal_cfg["url"] = portal_url
    portal_cfg["client_id"] = str(auth_data.get("clientId") or portal_cfg.get("client_id") or client_id)
    portal_cfg["api_key"] = str(auth_data.get("apiKey") or portal_cfg.get("api_key") or "")
    portal_cfg["node_uuid"] = str(node_data.get("uuid") or portal_cfg.get("node_uuid") or uuid_value)
    portal_cfg["node_slug"] = str(node_data.get("slug") or portal_cfg.get("node_slug") or slug_value)
    saved = _save_portal_config(portal_cfg)

    sync_ok, _, sync_result = _do_portal_sync(saved)
    if sync_ok:
        _trigger_async_media_sync_after_link(reason="relink")
    return jsonify(
        ok=bool(sync_result.get("ok")),
        relinked=True,
        node=node_data,
        auth={
            "clientId": portal_cfg.get("client_id"),
            "apiKeyPrefix": auth_data.get("apiKeyPrefix"),
            "apiKeyMasked": auth_data.get("apiKeyMasked"),
        },
        sync=sync_result,
    ), 200 if sync_ok else 502


@app.post("/api/portal/sync")
def api_portal_sync():
    ok, status, payload = _do_portal_sync()
    if ok:
        _trigger_async_media_sync_after_link(reason="portal_sync_api")
    intent_sync = _do_portal_mcp_intents_sync()
    body = dict(payload if isinstance(payload, dict) else {})
    body["mcpIntentSync"] = intent_sync
    return jsonify(body), (200 if ok else status)


@app.post("/api/portal/heartbeat")
def api_portal_heartbeat():
    ok, status, payload = _do_portal_heartbeat()
    return jsonify(payload), (200 if ok else status)


@app.post("/api/portal/reset")
def api_portal_reset():
    cfg = _load_portal_config()
    saved = _reset_portal_registration(
        keep_url=str(cfg.get("url") or ""),
        keep_machine_id=str(cfg.get("machine_id") or ""),
        keep_node_name=str(cfg.get("node_name") or ""),
    )
    return jsonify({
        "ok": True,
        "message": "Portal-Registrierung wurde zurückgesetzt.",
        "data": {
            "registered": bool(saved.get("client_id") and saved.get("api_key")),
            "portalUrl": saved.get("url") or "",
            "machineId": saved.get("machine_id") or "",
            "nodeName": saved.get("node_name") or "",
            "nodeUuid": saved.get("node_uuid") or "",
            "nodeSlug": saved.get("node_slug") or "",
        },
    }), 200


# ---------------------------------------------------------------------------
# Flask routes — git update
# ---------------------------------------------------------------------------

@app.get("/api/update/status")
def api_update_status():
    payload, status = _run_repo_update("status")
    return jsonify(payload), status


@app.post("/api/update/apply")
def api_update_apply():
    payload, status = _run_repo_update("apply")
    return jsonify(payload), status


# ---------------------------------------------------------------------------
# SSE realtime stream
# ---------------------------------------------------------------------------

@app.get("/api/display/realtime")
def api_display_realtime():
    def event_stream():
        while True:
            data = json.dumps({
                "video": video_manager.status(),
                "deviceplayer": _deviceplayer_health(),
                "timestamp": utc_now(),
            }, ensure_ascii=False)
            yield f"data: {data}\n\n"
            time.sleep(5)

    return Response(
        stream_with_context(event_stream()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/mcp-settings")
def mcp_settings_page():
    return redirect("/mcp-settings/overview", code=302)


@app.get("/mcp-settings/overview")
def mcp_settings_overview_page():
    return render_template("mcp_settings_overview.html", mcp_tab="overview", active_page="mcp")


@app.get("/mcp-settings/endpoints")
def mcp_settings_endpoints_page():
    return render_template("mcp_settings_endpoints.html", mcp_tab="endpoints", active_page="mcp")


@app.get("/mcp-settings/simple")
def mcp_settings_simple_page():
    return render_template("mcp_settings_simple.html", mcp_tab="simple", active_page="mcp")


@app.get("/mcp-settings/actions")
def mcp_settings_actions_page():
    return render_template("mcp_settings_actions.html", mcp_tab="actions", active_page="mcp")


@app.get("/mcp-settings/export")
def mcp_settings_export_page():
    return render_template("mcp_settings_export.html", mcp_tab="export", active_page="mcp")


def _validate_mcp_actions(items: Any) -> tuple[bool, str]:
    if not isinstance(items, list):
        return False, "Field 'actions' must be a list."
    for idx, a in enumerate(items):
        if not isinstance(a, dict):
            return False, f"actions[{idx}] must be an object."
        action_id = str(a.get("id") or "").strip()
        tool_name = str(a.get("tool_name") or "").strip()
        if not action_id or not tool_name:
            return False, f"actions[{idx}] requires 'id' and 'tool_name'."
        risk = str(a.get("risk_level") or "").strip().lower()
        enabled = bool(a.get("enabled", False))
        if risk == "dangerous" and enabled:
            return False, f"actions[{idx}] cannot be enabled when risk_level is 'dangerous'."
        endpoint_template = str(a.get("endpoint_template") or "").strip()
        if enabled and (
            endpoint_template.startswith("/api/credentials")
            or endpoint_template.startswith("/api/config")
            or endpoint_template.startswith("/api/update")
            or endpoint_template.startswith("/api/portal")
            or "/debug/" in endpoint_template
        ):
            return False, f"actions[{idx}] cannot be enabled for credentials/config/update/portal/debug endpoints."
        phase = str(a.get("phase") or "").strip()
        if phase and phase not in {"candidate", "readonly", "dry_run", "enabled"}:
            return False, f"actions[{idx}].phase must be one of: candidate, readonly, dry_run, enabled."
    return True, ""


def _permission_key_from_action(action: dict[str, Any]) -> str:
    action_key = str(action.get("tool_name") or action.get("id") or "").strip()
    if not action_key:
        return ""
    return hashlib.sha1(action_key.encode("utf-8")).hexdigest()[:8]


def _apply_permission_keys(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in actions or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["permission"] = _permission_key_from_action(row)
        out.append(row)
    return out


@app.get("/api/mcp/endpoints")
def api_mcp_endpoints():
    endpoints = mcp_registry.load_mcp_endpoints()
    if not endpoints:
        endpoints = mcp_registry.discover_flask_endpoints(app)
        mcp_registry.save_mcp_endpoints(endpoints)
    return jsonify(ok=True, count=len(endpoints), endpoints=endpoints)


@app.post("/api/mcp/endpoints/refresh")
def api_mcp_endpoints_refresh():
    endpoints = mcp_registry.discover_flask_endpoints(app)
    mcp_registry.save_mcp_endpoints(endpoints)
    summary: dict[str, Any] = {"total": len(endpoints), "by_category": {}, "by_risk": {}}
    for e in endpoints:
        cat = str(e.get("category") or "unknown")
        risk = str(e.get("risk_level") or "unknown")
        summary["by_category"][cat] = int(summary["by_category"].get(cat, 0)) + 1
        summary["by_risk"][risk] = int(summary["by_risk"].get(risk, 0)) + 1
    mcp_audit.write_mcp_audit("endpoint_refresh", {"summary": summary})
    return jsonify(ok=True, summary=summary)


@app.get("/api/mcp/actions")
def api_mcp_actions():
    actions = mcp_registry.load_mcp_actions()
    # Auto-migrate legacy smarthome mappings to domain-specific actions.
    if any(str(a.get("tool_name") or "").startswith("smarthome.") for a in actions if isinstance(a, dict)):
        endpoints = mcp_registry.load_mcp_endpoints()
        if not endpoints:
            endpoints = mcp_registry.discover_flask_endpoints(app)
            mcp_registry.save_mcp_endpoints(endpoints)
        actions = mcp_registry.generate_light_action_candidates(endpoints, existing_actions=[])
        mcp_registry.save_mcp_actions(actions)
        mcp_audit.write_mcp_audit("action_migrate_legacy_smarthome", {"count": len(actions)})
    normalized = _apply_permission_keys(actions)
    if normalized != actions:
        mcp_registry.save_mcp_actions(normalized)
    return jsonify(ok=True, count=len(normalized), actions=normalized)


@app.post("/api/mcp/actions/generate-light-candidates")
def api_mcp_actions_generate_light_candidates():
    endpoints = mcp_registry.load_mcp_endpoints()
    if not endpoints:
        endpoints = mcp_registry.discover_flask_endpoints(app)
        mcp_registry.save_mcp_endpoints(endpoints)
    existing = mcp_registry.load_mcp_actions()
    generated = mcp_registry.generate_light_action_candidates(endpoints, existing_actions=existing)
    generated = _apply_permission_keys(generated)
    mcp_registry.save_mcp_actions(generated)
    summary = {"endpoints_count": len(endpoints), "actions_total": len(generated)}
    mcp_audit.write_mcp_audit("action_generate", {"summary": summary})
    return jsonify(ok=True, summary=summary, count=len(generated), actions=generated)


@app.post("/api/mcp/actions/save")
def api_mcp_actions_save():
    body = request.get_json(silent=True) or {}
    actions = body.get("actions")
    ok, err = _validate_mcp_actions(actions)
    if not ok:
        return _api_err("bad_request", err, 400)
    normalized = _apply_permission_keys(actions)
    mcp_registry.save_mcp_actions(normalized)
    mcp_audit.write_mcp_audit("action_save", {"count": len(normalized or [])})
    return jsonify(ok=True, count=len(normalized or []), actions=normalized)


@app.get("/api/mcp/export")
def api_mcp_export():
    actions = mcp_registry.load_mcp_actions()
    payload = mcp_registry.export_enabled_mcp_tools(actions)
    mcp_audit.write_mcp_audit("export_config", {"enabled_count": len(payload.get("actions") or [])})
    return jsonify(ok=True, export=payload)


def _build_mcp_intents_payload() -> list[dict[str, Any]]:
    actions = mcp_registry.load_mcp_actions()
    exported = mcp_registry.export_enabled_mcp_tools(actions)
    out: list[dict[str, Any]] = []
    for action in exported.get("actions") or []:
        if not isinstance(action, dict):
            continue
        action_key = str(action.get("tool_name") or action.get("id") or "").strip().lower()
        if not action_key:
            continue
        out.append(
            {
                "intentKey": action_key.replace(".", "_"),
                "actionKey": action_key,
                "name": str(action.get("display_name") or action_key),
                "description": str(action.get("description") or ""),
                "permissionKey": str(action.get("permission") or ""),
                "operation": str(action.get("operation") or ""),
                "capability": str(action.get("capability") or ""),
                "httpMethod": str(action.get("http_method") or ""),
                "endpointTemplate": str(action.get("endpoint_template") or ""),
                "phase": str(action.get("phase") or ""),
                "riskLevel": str(action.get("risk_level") or ""),
                "requiredParams": action.get("required_params") if isinstance(action.get("required_params"), list) else [],
                "optionalParams": action.get("optional_params") if isinstance(action.get("optional_params"), list) else [],
                "source": "mcp",
            }
        )
    return out


def _do_portal_mcp_intents_sync(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or _load_portal_config()
    portal_url = str(cfg.get("url") or "").strip()
    client_id = str(cfg.get("client_id") or "").strip()
    api_key = str(cfg.get("api_key") or "").strip()
    if not portal_url or not client_id or not api_key:
        return {"ok": False, "error": "not_registered", "message": "Portal-Credentials fehlen."}
    payload = {"clientId": client_id, "apiKey": api_key, "intents": _build_mcp_intents_payload()}
    ok, status_code, resp, err = _http_post_json(
        f"{portal_url.rstrip('/')}/api/jarvis/node/intents/sync", payload, timeout=15
    )
    if not ok and not resp:
        return {"ok": False, "error": "portal_unreachable", "message": str(err)}
    if not bool(resp.get("ok")):
        return {
            "ok": False,
            "error": "sync_failed",
            "status": int(status_code or 502),
            "message": str(resp.get("message") or "MCP-Intent-Sync fehlgeschlagen."),
            "detail": resp,
        }
    return {"ok": True, "response": resp}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT", "5092"))
    debug = os.getenv("FLASK_DEBUG", "0") in {"1", "true", "True"}
    RUNTIME_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host=host, port=port, debug=debug)
