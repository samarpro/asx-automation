from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .models import InputPayload


class InputFileError(ValueError):
    pass


def load_input_file(input_file: str | Path) -> InputPayload:
    file_path = Path(input_file)
    if not file_path.exists():
        raise InputFileError(f"Input file not found: {file_path}")

    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputFileError(f"Invalid JSON in {file_path}: {exc}") from exc

    try:
        return InputPayload.model_validate(raw)
    except ValidationError as exc:
        raise InputFileError(f"Input schema validation failed: {exc}") from exc
