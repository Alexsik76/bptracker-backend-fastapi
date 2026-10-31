import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

YYYY_MM_PATTERN = re.compile(r"^\d{4}-\d{2}$")


class PathTraversalError(Exception):
    """Raised when a photo path attempts directory traversal."""


def get_safe_photo_path(ocr_photos_dir: Path, photo_path: str) -> Path:
    """Resolve a photo path relative to ocr_photos_dir, guarding against path traversal."""
    base = ocr_photos_dir.resolve()
    # Strip any leading slashes to prevent Path joining from treating it as root
    clean_path = photo_path.lstrip("/")
    candidate = (base / clean_path).resolve()
    if not candidate.is_relative_to(base) or candidate == base:
        raise PathTraversalError(f"Path traversal detected: {photo_path}")
    return candidate


def parse_timestamp(raw_ts: Any) -> datetime | None:
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        try:
            return datetime.fromtimestamp(raw_ts, tz=UTC)
        except (ValueError, OverflowError, OSError):
            return None
    if isinstance(raw_ts, str):
        try:
            dt = datetime.fromisoformat(raw_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
    return None


def scan_photo_storage(ocr_photos_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """Scan ocr_photos_dir for <YYYY-MM>/<stem>.jpg with sidecar <stem>.json.

    Other subfolders (e.g. 'labels') are ignored.
    Returns:
        (valid_sample_dicts, skipped_files_count)
    """
    valid_samples: list[dict[str, Any]] = []
    skipped_count = 0

    if not ocr_photos_dir.is_dir():
        return valid_samples, skipped_count

    for folder in sorted(ocr_photos_dir.iterdir()):
        if not folder.is_dir():
            continue
        # Only process folders matching YYYY-MM
        if not YYYY_MM_PATTERN.match(folder.name):
            continue

        for file_entry in sorted(folder.iterdir()):
            if not file_entry.is_file():
                continue
            if file_entry.suffix.lower() not in (".jpg", ".jpeg"):
                continue

            json_sidecar = folder / f"{file_entry.stem}.json"
            if not json_sidecar.is_file():
                skipped_count += 1
                continue

            try:
                data = json.loads(json_sidecar.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    skipped_count += 1
                    continue
            except Exception:
                logger.warning("Failed to parse sidecar JSON: %s", json_sidecar)
                skipped_count += 1
                continue

            rel_photo_path = f"{folder.name}/{file_entry.name}"
            raw_session = data.get("session")
            session = str(raw_session).strip() if raw_session else folder.name

            ref_sys = data.get("sys")
            if ref_sys is not None:
                try:
                    ref_sys = int(ref_sys)
                except (ValueError, TypeError):
                    ref_sys = None

            ref_dia = data.get("dia")
            if ref_dia is not None:
                try:
                    ref_dia = int(ref_dia)
                except (ValueError, TypeError):
                    ref_dia = None

            ref_pul = data.get("pul")
            if ref_pul is not None:
                try:
                    ref_pul = int(ref_pul)
                except (ValueError, TypeError):
                    ref_pul = None

            valid_samples.append(
                {
                    "photo_path": rel_photo_path,
                    "ref_sys": ref_sys,
                    "ref_dia": ref_dia,
                    "ref_pul": ref_pul,
                    "taken_at": parse_timestamp(data.get("timestamp")),
                    "device_model": str(data["device_model"]) if data.get("device_model") else None,
                    "source": str(data["source"]) if data.get("source") else None,
                    "session": session,
                    "corrected_by_user": bool(data.get("corrected_by_user", False)),
                    "meta": data,
                }
            )

    return valid_samples, skipped_count


def save_model_artifacts(
    ocr_models_dir: Path,
    version: str,
    display_model_bytes: bytes,
    digit_model_bytes: bytes,
) -> tuple[str, str]:
    """Save display and digit ONNX model files under ocr_models_dir/<version>/."""
    base = ocr_models_dir.resolve()
    clean_version = re.sub(r"[^\w\-.]", "_", version)
    target_dir = (base / clean_version).resolve()
    if not target_dir.is_relative_to(base):
        raise PathTraversalError(f"Invalid model version path: {version}")

    target_dir.mkdir(parents=True, exist_ok=True)
    display_file = target_dir / "display_model.onnx"
    digit_file = target_dir / "digit_model.onnx"

    display_file.write_bytes(display_model_bytes)
    digit_file.write_bytes(digit_model_bytes)

    rel_display = f"{clean_version}/display_model.onnx"
    rel_digit = f"{clean_version}/digit_model.onnx"
    return rel_display, rel_digit
