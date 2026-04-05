from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse


MIME_EXTENSION_MAP = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "text/csv": ".csv",
}


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "company"


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _infer_extension(
    url: str,
    response_headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> str:
    # First prefer an explicit filename advertised by the CDN.
    if response_headers:
        content_disposition = response_headers.get("content-disposition", "")
        match = re.search(r"filename\\*?=(?:UTF-8''|\")?([^\";]+)", content_disposition, re.IGNORECASE)
        if match:
            filename = Path(match.group(1).strip().strip("\""))
            suffix = filename.suffix.lower()
            if suffix:
                return suffix

        content_type = response_headers.get("content-type", "").split(";")[0].strip().lower()
        mapped = MIME_EXTENSION_MAP.get(content_type)
        if mapped:
            return mapped

    # Then inspect well-known file signatures.
    if content:
        if content.startswith(b"%PDF-"):
            return ".pdf"
        if content.startswith(b"PK\\x03\\x04"):
            # ZIP container (docx/xlsx/pptx or zip). Keep a safe generic default.
            return ".zip"
        if content.startswith(b"\\xD0\\xCF\\x11\\xE0"):
            # Legacy MS Office compound file format.
            return ".doc"

    # Finally fall back to URL path suffix if present.
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix:
        return suffix
    return ".bin"


def build_filename(
    date_prefix: str | None,
    title: str,
    url: str,
    index: int,
    response_headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> str:
    safe_title = slugify(title)[:80]
    prefix = f"{date_prefix}_" if date_prefix else ""
    extension = _infer_extension(url, response_headers=response_headers, content=content)
    return f"{prefix}{safe_title}_{index:03d}{extension}"


class DownloadIndex:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.root_dir / ".download_index.json"
        self._index = self._read()

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.index_file.exists():
            return {}
        try:
            return json.loads(self.index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(self) -> None:
        self.index_file.write_text(json.dumps(self._index, indent=2), encoding="utf-8")

    def has_url(self, url: str) -> bool:
        return url in self._index

    def record(self, url: str, saved_path: Path, digest: str) -> None:
        self._index[url] = {"saved_path": str(saved_path), "sha256": digest}


def save_download_content(
    index: DownloadIndex,
    source_url: str,
    content: bytes,
    target_dir: Path,
    filename: str,
) -> tuple[Literal["downloaded", "skipped"], str]:
    if index.has_url(source_url):
        return "skipped", index._index[source_url]["saved_path"]

    digest = _hash_bytes(content)
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / filename
    if file_path.exists():
        existing_digest = _hash_bytes(file_path.read_bytes())
        if existing_digest == digest:
            index.record(source_url, file_path, digest)
            index.save()
            return "skipped", str(file_path)

        stem = file_path.stem
        suffix = file_path.suffix
        file_path = target_dir / f"{stem}_{digest[:8]}{suffix}"

    file_path.write_bytes(content)
    index.record(source_url, file_path, digest)
    index.save()
    return "downloaded", str(file_path)
