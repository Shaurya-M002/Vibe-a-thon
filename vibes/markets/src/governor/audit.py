"""Atomic, private JSON artifacts shared by reports and the payment outbox."""

import json
import os
import tempfile
from pathlib import Path


def write_audit(data_dir: Path, session_id: str, report: dict) -> Path:
    directory = data_dir / "reports"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"{session_id}.json"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=directory, delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            json.dump(report, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
