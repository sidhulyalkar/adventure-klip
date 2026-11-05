"""Private, atomic, resumable per-candidate checkpoints for temporal evidence.

A journal does not prove visual measurements. Completed rows are reused only
when the original feature-template fingerprint, ordered candidate selection,
execution flags and current checksum-verified preview receipt still agree.
An interrupted item is NOT retried automatically (a removable drive may hang).
"""
from __future__ import annotations

from hashlib import sha256
from contextlib import contextmanager
import fcntl
from pathlib import Path
import json
import os
import tempfile

from .studio_preview_judge import preview_paths, _check_cached

VERSION = "social-temporal-checkpoint-v1"


def _digest_json(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    # Never directly truncate a successful checkpoint.
    fd, temp = tempfile.mkstemp(prefix=".klip-checkpoint-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, sort_keys=True, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _identity(c: dict) -> dict:
    return {key: c[key] for key in ("assetId", "startMs", "endMs", "sport")}


class TemporalCheckpoint:
    """One append-by-replacement local journal scoped to an immutable pilot."""

    def __init__(self, root: Path, destination: Path, feature_sha: str,
                 candidates: list[dict], *, run: bool, prepare_previews: bool,
                 resume: bool = False):
        self.root = Path(root).resolve()
        self.dest = Path(destination).expanduser().resolve()
        if (self.dest == self.root or not self.dest.is_relative_to(self.root)
                or self.dest.relative_to(self.root).parts[0] not in
                ("reports", "exports")):
            raise ValueError("Checkpoint output must remain in workspace reports/ or exports/")
        self.rows = self.dest / "checkpoints"
        self.meta = {
            "version": VERSION,
            "featureTemplateSha256": feature_sha,
            "selectedCandidateFingerprint": _digest_json(
                [_identity(c) for c in candidates]),
            "candidateCount": len(candidates),
            "run": bool(run),
            "preparePreviews": bool(prepare_previews),
        }
        self.resumed = resume
        if resume:
            if not self.dest.is_dir() or not self.rows.is_dir():
                raise ValueError("Cannot --resume: report directory lacks a Klip checkpoint journal")
            if (self.dest / "temporal-evidence.json").exists():
                raise ValueError("Completed report exists; use a NEW directory for another analysis")
            try:
                existing = json.loads((self.dest / "checkpoint.json").read_text())
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError("Cannot --resume: checkpoint metadata is missing or invalid") from exc
            if existing != self.meta:
                raise ValueError("Cannot --resume: features, candidate order, --limit or run flags differ")
        else:
            if self.dest.exists():
                raise ValueError("Output must be a NEW directory, or specify --resume for an existing Klip checkpoint")
            self.dest.mkdir(parents=True, mode=0o700, exist_ok=False)
            self.rows.mkdir(mode=0o700)
            _atomic_json(self.dest / "checkpoint.json", self.meta)

    @contextmanager
    def exclusive(self):
        """Refuse simultaneous writers to the same unfinished report."""
        lock = self.dest / "checkpoint.lock"
        with lock.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(
                    "This temporal checkpoint is already being processed by another Klip run"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _path(self, index: int, candidate: dict) -> Path:
        key = _digest_json(_identity(candidate))[:16]
        return self.rows / f"{index:04d}-{key}.json"

    def read(self, index: int, candidate: dict):
        path = self._path(index, candidate)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(
                f"Checkpoint {index} is damaged; preserve it and investigate, do not silently redo work"
            ) from exc
        if (not isinstance(record, dict) or record.get("version") != VERSION
                or record.get("index") != index
                or record.get("candidate") != _identity(candidate)
                or record.get("featureTemplateSha256") !=
                self.meta["featureTemplateSha256"]
                or record.get("state") not in
                ("in_progress", "success", "failure")):
            raise ValueError(f"Checkpoint {index} identity/schema mismatch")
        if record["state"] == "success":
            row = record.get("result")
            if (not isinstance(row, dict)
                    or any(row.get(k) != candidate[k] for k in
                           ("assetId", "startMs", "endMs", "sport"))
                    or type(row.get("previewVerified")) is not bool
                    or (row.get("previewVerified") is True and
                        (type(record.get("previewSha256")) is not str or
                         len(record["previewSha256"]) != 64))):
                raise ValueError(f"Checkpoint {index} completed row is malformed")
        if record["state"] == "failure" and not isinstance(record.get("result"), dict):
            raise ValueError(f"Checkpoint {index} error row is malformed")
        return record

    def may_reuse_success(self, record: dict, candidate: dict) -> bool:
        row = record["result"]
        # A previous positive receipt must still be SHA-valid. A previously
        # missing preview becoming available invalidates the old answer.
        path, receipt = preview_paths(self.root, candidate["assetId"],
                                      candidate["startMs"], candidate["endMs"])
        valid = _check_cached(path, receipt, aid=candidate["assetId"],
                              start_ms=candidate["startMs"],
                              end_ms=candidate["endMs"])
        if valid != row["previewVerified"]:
            return False
        if not valid:
            return True
        # Re-check actual video identity, not only that *some* valid receipt
        # exists. A replaced video + updated receipt must invalidate metrics.
        return sha256(path.read_bytes()).hexdigest() == record["previewSha256"]

    def store(self, index: int, candidate: dict, state: str, result=None,
              *, original_attempt: bool = False) -> None:
        if state not in ("in_progress", "success", "failure"):
            raise ValueError("Invalid checkpoint state")
        if state != "in_progress" and not isinstance(result, dict):
            raise ValueError("A finished checkpoint requires a result")
        payload = {
            "version": VERSION, "index": index,
            "featureTemplateSha256": self.meta["featureTemplateSha256"],
            "candidate": _identity(candidate),
            "state": state,
            "originalSourceReadAttempted": bool(original_attempt),
        }
        if result is not None:
            payload["result"] = result
        if state == "success" and result["previewVerified"]:
            preview, receipt = preview_paths(
                self.root, candidate["assetId"], candidate["startMs"], candidate["endMs"])
            if not _check_cached(preview, receipt, aid=candidate["assetId"],
                                 start_ms=candidate["startMs"], end_ms=candidate["endMs"]):
                raise RuntimeError("Preview changed during temporal inference; result not checkpointed")
            payload["previewSha256"] = sha256(preview.read_bytes()).hexdigest()
        _atomic_json(self._path(index, candidate), payload)
