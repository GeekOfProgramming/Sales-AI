"""Admin-Gate Ingestion Queue Manager for pyBIM-LLM.
Provides structured local storage (data/pending_ingestions.json) for 2-stage verification
of incoming documentation before committing into ChromaDB.
"""

import json
import secrets
from pathlib import Path
from datetime import datetime, timezone
from threading import Lock
from typing import Optional, List, Dict, Any

QUEUE_FILE_PATH = Path(__file__).parent.parent / "data" / "pending_ingestions.json"
_lock = Lock()


class IngestQueueManager:
    """Thread-safe persistent queue for pending knowledge ingestion requests."""

    def __init__(self, file_path: Path = QUEUE_FILE_PATH):
        self.file_path = file_path
        self._ensure_storage()

    def _ensure_storage(self):
        """Ensure parent directory and JSON file exist."""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

    def _read_all(self) -> List[Dict[str, Any]]:
        """Read all queue items."""
        self._ensure_storage()
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _write_all(self, items: List[Dict[str, Any]]):
        """Write all queue items back to JSON."""
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)

    def enqueue(self, url: str, slug: Optional[str] = None, submitter: str = "Revit Client / Web User") -> Dict[str, Any]:
        """Enqueue a new documentation URL for admin review."""
        with _lock:
            items = self._read_all()
            tracking_id = f"req_{secrets.token_hex(4)}"
            item = {
                "request_id": tracking_id,
                "url": url,
                "slug": slug,
                "status": "pending",  # pending | processing | approved | rejected
                "submitter": submitter,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "processed_at": None,
                "message": "Awaiting administrator approval before indexing into ChromaDB.",
            }
            items.insert(0, item)  # Newest first
            self._write_all(items)
            return item

    def list_items(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """List queue items, optionally filtered by status."""
        with _lock:
            items = self._read_all()
            if status_filter:
                return [it for it in items if it.get("status") == status_filter]
            return items

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Get a single queue item by tracking ID."""
        with _lock:
            items = self._read_all()
            for it in items:
                if it.get("request_id") == request_id:
                    return it
            return None

    def update_status(self, request_id: str, status: str, message: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update the status and message of a queue item."""
        with _lock:
            items = self._read_all()
            for it in items:
                if it.get("request_id") == request_id:
                    it["status"] = status
                    it["processed_at"] = datetime.now(timezone.utc).isoformat()
                    if message is not None:
                        it["message"] = message
                    self._write_all(items)
                    return it
            return None

    def delete(self, request_id: str) -> bool:
        """Remove a queue item by request_id."""
        with _lock:
            items = self._read_all()
            initial_len = len(items)
            items = [it for it in items if it.get("request_id") != request_id]
            if len(items) != initial_len:
                self._write_all(items)
                return True
            return False


# Global default instance
ingest_queue = IngestQueueManager()
