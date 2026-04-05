from pathlib import Path

from asx_tool.downloads import DownloadIndex, build_filename, save_download_content, slugify


def test_slugify_basic():
    assert slugify("BHP Group Ltd") == "bhp-group-ltd"


def test_build_filename_has_ext():
    name = build_filename("2026-01-01", "Quarterly Update", "https://example.com/file.pdf", 1)
    assert name.endswith(".pdf")


def test_build_filename_uses_content_type_when_url_has_no_extension():
    name = build_filename(
        "2026-01-01",
        "Quarterly Update",
        "https://cdn-api.markitdigital.com/asx-research/1.0/file/2924-123",
        1,
        response_headers={"content-type": "application/pdf"},
    )
    assert name.endswith(".pdf")


def test_build_filename_uses_content_disposition_filename():
    name = build_filename(
        "2026-01-01",
        "Quarterly Update",
        "https://cdn-api.markitdigital.com/asx-research/1.0/file/2924-456",
        1,
        response_headers={"content-disposition": 'attachment; filename="notice.docx"'},
    )
    assert name.endswith(".docx")


def test_build_filename_uses_content_signature_fallback():
    name = build_filename(
        "2026-01-01",
        "Quarterly Update",
        "https://cdn-api.markitdigital.com/asx-research/1.0/file/2924-789",
        1,
        content=b"%PDF-1.7 sample",
    )
    assert name.endswith(".pdf")


def test_dedup_skips_existing_url(tmp_path: Path):
    index = DownloadIndex(tmp_path)
    status_1, path_1 = save_download_content(index, "https://example.com/a.pdf", b"123", tmp_path, "a.pdf")
    status_2, path_2 = save_download_content(index, "https://example.com/a.pdf", b"123", tmp_path, "a.pdf")

    assert status_1 == "downloaded"
    assert status_2 == "skipped"
    assert path_1 == path_2
