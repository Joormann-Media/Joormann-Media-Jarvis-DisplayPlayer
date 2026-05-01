from __future__ import annotations

from typing import Any, Callable


class MediaSyncService:
    def __init__(self, post_json: Callable[[str, dict[str, Any], int], tuple[bool, int, dict[str, Any], str]]) -> None:
        self._post_json = post_json

    def sync_folder_scan(
        self,
        portal_config: dict[str, Any],
        folder: dict[str, Any],
        scan_result: dict[str, Any],
        *,
        local_ip: str,
        flask_port: int,
        timestamp: str,
    ) -> dict[str, Any]:
        portal_url = str(portal_config.get("url") or "").strip()
        client_id = str(portal_config.get("client_id") or "").strip()
        api_key = str(portal_config.get("api_key") or "").strip()
        node_uuid = str(portal_config.get("node_uuid") or "").strip()
        node_name = str(portal_config.get("node_name") or "").strip()

        if not portal_url or not client_id or not api_key:
            return {
                "ok": False,
                "status": 400,
                "error": "not_registered",
                "message": "Portal-Credentials fehlen.",
                "synced_at": "",
            }

        all_files = scan_result.get("files") if isinstance(scan_result.get("files"), list) else []
        summary = scan_result.get("summary") if isinstance(scan_result.get("summary"), dict) else {}

        files = [f for f in all_files if isinstance(f, dict) and str(f.get("media_type") or "") in {"video", "image"}]
        video_count = sum(1 for f in files if f.get("media_type") == "video")
        image_count = sum(1 for f in files if f.get("media_type") == "image")

        payload = {
            "clientId": client_id,
            "apiKey": api_key,
            "nodeUuid": node_uuid,
            "nodeName": node_name,
            "nodeType": "display_player",
            "mediaSync": {
                "timestamp": timestamp,
                "apiBaseUrl": f"http://{local_ip}:{flask_port}",
                "folder": {
                    "id": str(folder.get("id") or ""),
                    "path": str(folder.get("path") or ""),
                    "label": str(folder.get("label") or ""),
                    "media_category": str(folder.get("media_category") or "mixed"),
                    "active": bool(folder.get("active", True)),
                },
                "summary": summary,
                "files": files,
                "video_count": video_count,
                "image_count": image_count,
            },
        }

        endpoint = f"{portal_url.rstrip('/')}/api/jarvis/node/media/sync"
        ok, status_code, response, err = self._post_json(endpoint, payload, 120)
        if not ok and not response:
            return {
                "ok": False,
                "status": 502,
                "error": "portal_unreachable",
                "message": str(err or "Portal nicht erreichbar"),
                "synced_at": "",
            }
        if not bool(response.get("ok")):
            return {
                "ok": False,
                "status": int(status_code or 502),
                "error": "media_sync_failed",
                "message": str(response.get("message") or "Media-Sync fehlgeschlagen"),
                "detail": response,
                "synced_at": "",
            }
        return {
            "ok": True,
            "status": 200,
            "synced_at": timestamp,
            "response": response,
        }
