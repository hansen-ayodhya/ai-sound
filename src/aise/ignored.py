"""Rekomendasi yang diabaikan operator: tidak ditampilkan dan tidak dilacak lagi, sampai dikembalikan."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MAX_ITEMS = 200


def key_for(f: dict) -> str:
    """Kunci stabil: channel + himpunan alamat yang diusulkan (sama untuk aturan lokal dan Gemini); tanpa usulan: id."""
    addrs = [u["alamat"] for u in (f.get("usulan") or []) if isinstance(u, dict) and isinstance(u.get("alamat"), str)]
    if addrs:
        return f"{f.get('ch') if f.get('ch') is not None else '-'}|" + "+".join(sorted(addrs))
    return f"id:{f.get('id')}"


class Ignored:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d["items"] if isinstance(d.get("items"), dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self, items: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def keys(self) -> set[str]:
        return set(self._load())

    def add(self, f: dict) -> str:
        key = key_for(f)
        with self._lock:
            items = self._load()
            items[key] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ch": f.get("ch"), "masalah": str(f.get("masalah", ""))[:160], "id": str(f.get("id", ""))[:80]}
            for k in sorted(items, key=lambda k: items[k]["ts"])[: max(0, len(items) - MAX_ITEMS)]:
                del items[k]
            self._save(items)
        return key

    def restore(self, key: str) -> bool:
        with self._lock:
            items = self._load()
            if key not in items:
                return False
            del items[key]
            self._save(items)
            return True

    def listing(self) -> list[dict]:
        items = self._load()
        return [{"key": k, **v} for k, v in sorted(items.items(), key=lambda kv: kv[1]["ts"], reverse=True)]
