from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MediaFolderValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class MediaFolderRegistry:
    ALLOWED_MEDIA_CATEGORIES = {"movie", "tv_show", "series", "clips", "pictures", "mixed"}
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def load(self) -> dict[str, Any]:
        default = {"folders": []}
        if not self._config_path.exists():
            return default
        try:
            raw = json.loads(self._config_path.read_text(encoding="utf-8"))
        except Exception:
            return default
        if not isinstance(raw, dict):
            return default
        folders = raw.get("folders") if isinstance(raw.get("folders"), list) else []
        normalized: list[dict[str, Any]] = []
        for item in folders:
            if not isinstance(item, dict):
                continue
            normalized.append(self._normalize_folder(item))
        return {"folders": normalized}

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        folders = state.get("folders") if isinstance(state.get("folders"), list) else []
        payload = {"folders": [self._normalize_folder(item) for item in folders if isinstance(item, dict)]}
        _atomic_write_json(self._config_path, payload)
        return payload

    def list_folders(self) -> list[dict[str, Any]]:
        state = self.load()
        folders = state["folders"]
        folders.sort(key=lambda x: str(x.get("path") or "").lower())
        return folders

    def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        folder_id = str(folder_id or "").strip()
        if not folder_id:
            return None
        for item in self.list_folders():
            if str(item.get("id") or "") == folder_id:
                return item
        return None

    def add_folder(self, path: str, label: str = "", media_category: str = "mixed") -> dict[str, Any]:
        normalized_path = self.validate_media_path(path)
        now = utc_now()
        normalized_category = self.normalize_media_category(media_category)
        state = self.load()
        folders = state["folders"]
        for item in folders:
            if str(item.get("path") or "") == normalized_path:
                if label.strip() and not str(item.get("label") or "").strip():
                    item["label"] = label.strip()
                item["media_category"] = normalized_category
                item["updated_at"] = now
                self.save(state)
                return self._normalize_folder(item)

        entry = {
            "id": uuid.uuid4().hex,
            "path": normalized_path,
            "label": label.strip() or Path(normalized_path).name or normalized_path,
            "media_category": normalized_category,
            "active": True,
            "file_count": 0,
            "media_count": 0,
            "last_scan_at": "",
            "last_scan_status": "never",
            "last_scan_error": "",
            "last_scan_summary": {},
            "last_scan_preview": [],
            "last_panel_sync_at": "",
            "last_panel_sync_status": "never",
            "last_panel_sync_error": "",
            "created_at": now,
            "updated_at": now,
        }
        folders.append(entry)
        self.save(state)
        return self._normalize_folder(entry)

    def remove_folder(self, folder_id: str) -> bool:
        state = self.load()
        folders = state["folders"]
        next_folders = [item for item in folders if str(item.get("id") or "") != str(folder_id or "")]
        if len(next_folders) == len(folders):
            return False
        state["folders"] = next_folders
        self.save(state)
        return True

    def update_folder(self, folder_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        state = self.load()
        folders = state["folders"]
        now = utc_now()
        for item in folders:
            if str(item.get("id") or "") != str(folder_id or ""):
                continue
            for key, value in patch.items():
                if key in {"id", "created_at", "path"}:
                    continue
                if key == "media_category":
                    item[key] = self.normalize_media_category(value)
                    continue
                item[key] = value
            item["updated_at"] = now
            self.save(state)
            return self._normalize_folder(item)
        return None

    def validate_media_path(self, raw_path: str) -> str:
        import os as _os
        source = str(raw_path or "").strip()
        if not source:
            raise MediaFolderValidationError("Pfad fehlt.")

        candidate = Path(source)
        if not candidate.is_absolute():
            raise MediaFolderValidationError("Nur absolute Pfade sind erlaubt.")

        normalized = candidate.resolve(strict=False)
        normalized_str = str(normalized)

        allowed_raw = str(_os.getenv("DISPLAYPLAYER_MEDIA_ALLOWED_PREFIXES", "")).strip()
        if allowed_raw:
            allowed = [p.strip() for p in allowed_raw.split(",") if p.strip()]
            if not any(normalized_str == p or normalized_str.startswith(p.rstrip("/") + "/") for p in allowed):
                raise MediaFolderValidationError(f"Pfad liegt nicht in einem erlaubten Verzeichnis ({', '.join(allowed)}).")

        if not normalized.exists():
            raise MediaFolderValidationError("Pfad existiert nicht.")
        if not normalized.is_dir():
            raise MediaFolderValidationError("Pfad ist kein Verzeichnis.")
        return normalized_str

    def _normalize_folder(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(raw.get("id") or "").strip(),
            "path": str(raw.get("path") or "").strip(),
            "label": str(raw.get("label") or "").strip(),
            "media_category": self.normalize_media_category(raw.get("media_category")),
            "active": bool(raw.get("active", True)),
            "file_count": int(raw.get("file_count") or 0),
            "media_count": int(raw.get("media_count") or 0),
            "video_count": int(raw.get("video_count") or 0),
            "image_count": int(raw.get("image_count") or 0),
            "last_scan_at": str(raw.get("last_scan_at") or "").strip(),
            "last_scan_status": str(raw.get("last_scan_status") or "never").strip() or "never",
            "last_scan_error": str(raw.get("last_scan_error") or "").strip(),
            "last_scan_summary": raw.get("last_scan_summary") if isinstance(raw.get("last_scan_summary"), dict) else {},
            "last_scan_preview": raw.get("last_scan_preview") if isinstance(raw.get("last_scan_preview"), list) else [],
            "last_panel_sync_at": str(raw.get("last_panel_sync_at") or "").strip(),
            "last_panel_sync_status": str(raw.get("last_panel_sync_status") or "never").strip() or "never",
            "last_panel_sync_error": str(raw.get("last_panel_sync_error") or "").strip(),
            "created_at": str(raw.get("created_at") or "").strip(),
            "updated_at": str(raw.get("updated_at") or "").strip(),
        }

    def normalize_media_category(self, value: Any) -> str:
        category = str(value or "mixed").strip().lower().replace("-", "_")
        if category not in self.ALLOWED_MEDIA_CATEGORIES:
            return "mixed"
        return category
