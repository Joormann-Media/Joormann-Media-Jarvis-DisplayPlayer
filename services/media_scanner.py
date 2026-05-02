from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCANABLE_MAX_BYTES_FOR_HASH = 64 * 1024 * 1024


class MediaScanner:
    def __init__(self, file_limit: int = 5000, hash_enabled: bool = False) -> None:
        self._file_limit = max(100, int(file_limit or 5000))
        self._hash_enabled = bool(hash_enabled)

    def scan_folder(self, folder_path: str, media_category: str = "mixed") -> dict[str, Any]:
        root = Path(str(folder_path)).resolve()
        normalized_category = str(media_category or "mixed").strip().lower().replace("-", "_")
        started_at = _utc_now()
        summary: dict[str, Any] = {
            "folder_path": str(root),
            "started_at": started_at,
            "finished_at": "",
            "status": "ok",
            "error": "",
            "file_count": 0,
            "media_count": 0,
            "counts_by_type": {
                "audio": 0,
                "video": 0,
                "image": 0,
                "document": 0,
                "other": 0,
            },
            "truncated": False,
        }
        files: list[dict[str, Any]] = []

        if not root.exists() or not root.is_dir():
            summary["status"] = "error"
            summary["error"] = "Ordner existiert nicht oder ist kein Verzeichnis."
            summary["finished_at"] = _utc_now()
            return {"summary": summary, "files": files}

        try:
            for current_root, _, names in os.walk(root, topdown=True, followlinks=False):
                base = Path(current_root)
                for name in names:
                    fp = base / name
                    if not fp.is_file():
                        continue

                    try:
                        stat = fp.stat()
                    except Exception:
                        continue

                    media_type, mime_type = classify_file(fp)
                    rel_path = str(fp.relative_to(root))
                    derived = _derive_media_metadata(rel_path, fp.stem, media_type, normalized_category)
                    info = {
                        "name": fp.name,
                        "relative_path": rel_path,
                        "absolute_path": str(fp),
                        "extension": fp.suffix.lower().lstrip("."),
                        "mime_type": mime_type,
                        "size_bytes": int(stat.st_size),
                        "modified_at": _to_iso(stat.st_mtime),
                        "source_folder": str(root),
                        "media_type": media_type,
                        "media_kind": derived["media_kind"],
                        "title": derived["title"],
                    }
                    if derived["series_name"]:
                        info["series_name"] = derived["series_name"]
                    if derived["season_number"] is not None:
                        info["season_number"] = derived["season_number"]
                    if derived["episode_number"] is not None:
                        info["episode_number"] = derived["episode_number"]
                    if self._hash_enabled and stat.st_size <= SCANABLE_MAX_BYTES_FOR_HASH:
                        info["hash_sha1"] = _sha1_file(fp)

                    files.append(info)
                    summary["file_count"] += 1
                    if media_type in {"audio", "video", "image", "document"}:
                        summary["media_count"] += 1
                    summary["counts_by_type"][media_type] = int(summary["counts_by_type"].get(media_type, 0)) + 1

                    if len(files) >= self._file_limit:
                        summary["truncated"] = True
                        break
                if summary["truncated"]:
                    break
        except Exception as exc:
            summary["status"] = "error"
            summary["error"] = str(exc)

        summary["finished_at"] = _utc_now()
        return {"summary": summary, "files": files}


def classify_file(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    mime_guess, _ = mimetypes.guess_type(str(path), strict=False)
    mime_type = str(mime_guess or "").strip().lower()

    audio_ext = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".mka", ".m3u", ".m3u8", ".pls"}
    video_ext = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".m4v", ".mpeg", ".mpg", ".wmv", ".flv"}
    image_ext = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff", ".avif"}
    document_ext = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt", ".ods", ".csv", ".json", ".xml"}

    if mime_type.startswith("video/") or ext in video_ext:
        return "video", mime_type or "video/unknown"
    if mime_type.startswith("audio/") or ext in audio_ext:
        return "audio", mime_type or "audio/unknown"
    if mime_type.startswith("image/") or ext in image_ext:
        return "image", mime_type or "image/unknown"
    if mime_type.startswith("text/") or mime_type in {"application/pdf", "application/msword"} or ext in document_ext:
        return "document", mime_type or "application/octet-stream"
    return "other", mime_type or "application/octet-stream"


def _derive_media_metadata(relative_path: str, file_stem: str, media_type: str, media_category: str) -> dict[str, Any]:
    parts = [p.strip() for p in str(relative_path).replace("\\", "/").split("/") if p.strip()]
    top = parts[0] if parts else ""
    season_hint = parts[1] if len(parts) > 1 else ""
    name_source = top or file_stem

    cleaned_series = _humanize_title(name_source)
    cleaned_title = _humanize_title(file_stem)
    season_no, episode_no = _parse_season_episode(relative_path, file_stem)

    if media_type != "video":
        media_kind = "image" if media_type == "image" else ("track" if media_type == "audio" else "other")
    elif media_category in {"series", "tv_show"}:
        media_kind = "series"
    elif media_category == "clips":
        media_kind = "clip"
    elif media_category == "movie":
        media_kind = "movie"
    elif season_no is not None or episode_no is not None:
        media_kind = "series"
    elif _looks_like_series_path(relative_path):
        media_kind = "series"
    else:
        media_kind = "movie"

    if media_kind == "series":
        display_title = cleaned_title
        if season_no is not None and episode_no is not None:
            display_title = f"{cleaned_series} S{season_no:02d}E{episode_no:02d}"
        elif season_no is not None:
            display_title = f"{cleaned_series} S{season_no:02d}"
        elif season_hint:
            display_title = f"{cleaned_series} · {season_hint}"
        else:
            display_title = cleaned_series or cleaned_title
    else:
        display_title = cleaned_title

    return {
        "media_kind": media_kind,
        "title": display_title,
        "series_name": cleaned_series if media_kind == "series" and cleaned_series else "",
        "season_number": season_no,
        "episode_number": episode_no,
    }


def _parse_season_episode(relative_path: str, file_stem: str) -> tuple[int | None, int | None]:
    haystack = f"{relative_path} {file_stem}"
    m = re.search(r"(?i)(?:^|[^a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?:[^a-z0-9]|$)", haystack)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(?i)(?:^|[^a-z0-9])(\d{1,2})x(\d{1,3})(?:[^a-z0-9]|$)", haystack)
    if m:
        return int(m.group(1)), int(m.group(2))
    m_season = re.search(r"(?i)(?:^|[^a-z0-9])(season|staffel)[ ._-]*(\d{1,2})(?:[^a-z0-9]|$)", haystack)
    if m_season:
        return int(m_season.group(2)), None
    return None, None


def _looks_like_series_path(relative_path: str) -> bool:
    p = relative_path.lower()
    return any(token in p for token in ["/staffel", "/season", "s01e", "s02e", "s03e", "episode", "tv_show"])


def _humanize_title(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"[._]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_")
    return text


def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
