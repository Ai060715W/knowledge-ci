from __future__ import annotations

"""Stable read/write interface for Knowledge CI data.

Every consumer talks to files through this layer instead of raw ``json`` calls,
so the storage layout can change (e.g. PostgreSQL later) without touching the
pipeline. Writes are atomic (temp file + ``os.replace``) and serialized with a
per-store lock.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from src.registry.schema import RegistryValidationError, validate_registry

__all__ = ["RegistryStore", "atomic_write_json"]


def atomic_write_json(path: str | Path, data: Any) -> Path:
    """Write JSON atomically: temp file in the same directory, then rename."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(data, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
        os.replace(temp_path, target)
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return target


class RegistryStore:
    """JSON-backed knowledge store with the v2 data layout.

    Paths not provided are left unset (their accessors return ``None``), so a
    store can be built with only the registry path for pure-registry work.
    """

    def __init__(
        self,
        registry_path: str | Path,
        evidence_path: str | Path | None = None,
        patches_path: str | Path | None = None,
        reports_path: str | Path | None = None,
        metrics_path: str | Path | None = None,
        feedback_path: str | Path | None = None,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.evidence_path = Path(evidence_path) if evidence_path else None
        self.patches_path = Path(patches_path) if patches_path else None
        self.reports_path = Path(reports_path) if reports_path else None
        self.metrics_path = Path(metrics_path) if metrics_path else None
        self.feedback_path = Path(feedback_path) if feedback_path else None
        self._lock = threading.Lock()

    # -- registry -----------------------------------------------------------

    def load_registry(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def save_registry(self, registry: dict[str, Any], validate: bool = True) -> Path:
        if validate:
            errors = validate_registry(registry)
            if errors:
                raise RegistryValidationError(
                    "Registry failed schema validation:\n- " + "\n- ".join(errors)
                )
        with self._lock:
            return atomic_write_json(self.registry_path, registry)

    def find_unit(self, unit_id: str) -> dict[str, Any] | None:
        registry = self.load_registry()
        return next(
            (unit for unit in registry.get("units", []) if unit.get("id") == unit_id),
            None,
        )

    def upsert_unit(self, unit: dict[str, Any], validate: bool = True) -> dict[str, Any]:
        registry = self.load_registry()
        replaced = False
        for index, existing in enumerate(registry.get("units", [])):
            if existing.get("id") == unit.get("id"):
                registry["units"][index] = unit
                replaced = True
                break
        if not replaced:
            registry.setdefault("units", []).append(unit)
        self.save_registry(registry, validate=validate)
        return unit

    def remove_unit(self, unit_id: str) -> dict[str, Any] | None:
        registry = self.load_registry()
        remaining = [unit for unit in registry.get("units", []) if unit.get("id") != unit_id]
        removed = (
            next((unit for unit in registry.get("units", []) if unit.get("id") == unit_id), None)
        )
        if removed is None:
            return None
        registry["units"] = remaining
        self.save_registry(registry)
        return removed

    # -- derived data directories ------------------------------------------

    def evidence_dir(self) -> Path | None:
        return self.evidence_path

    def save_evidence(self, key: str, data: dict[str, Any]) -> Path:
        if self.evidence_path is None:
            raise RuntimeError("evidence_path is not configured on this store.")
        safe_key = "".join(char for char in key if char.isalnum() or char in "._-")
        if not safe_key or safe_key != key:
            raise ValueError(f"Evidence key must be alphanumeric with ._- only: {key!r}")
        return atomic_write_json(self.evidence_path / f"{safe_key}.json", data)

    def load_evidence(self, key: str) -> dict[str, Any] | None:
        if self.evidence_path is None:
            return None
        path = self.evidence_path / f"{key}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def metrics_dir(self) -> Path | None:
        return self.metrics_path

    def save_metrics(self, key: str, data: dict[str, Any]) -> Path:
        if self.metrics_path is None:
            raise RuntimeError("metrics_path is not configured on this store.")
        safe_key = "".join(char for char in key if char.isalnum() or char in "._-")
        if not safe_key or safe_key != key:
            raise ValueError(f"Metrics key must be alphanumeric with ._- only: {key!r}")
        return atomic_write_json(self.metrics_path / f"{safe_key}.json", data)
