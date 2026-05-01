from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mcp_registry import mask_sensitive_data


REPO_ROOT = Path(__file__).resolve().parent
AUDIT_PATH = REPO_ROOT / "config" / "mcp_audit.local.jsonl"


def write_mcp_audit(event_type: str, payload: Any) -> None:
    event = {
        "ts": int(time.time()),
        "event_type": str(event_type or "").strip() or "unknown",
        "payload": mask_sensitive_data(payload),
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

