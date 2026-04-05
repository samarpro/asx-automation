from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CompanyQuery(BaseModel):
    ticker: str | None = Field(default=None, description="ASX ticker, e.g. BHP")
    company_name: str | None = Field(default=None, description="Company display name")
    date_from: date | None = Field(default=None, description="Start date (inclusive)")
    date_to: date | None = Field(default=None, description="End date (inclusive)")

    @model_validator(mode="after")
    def validate_company_identity(self) -> "CompanyQuery":
        if not self.ticker and not self.company_name:
            raise ValueError("Each company requires ticker and/or company_name")

        if self.ticker:
            self.ticker = self.ticker.strip().upper()
        if self.company_name:
            self.company_name = self.company_name.strip()

        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be after date_to")

        return self


class InputPayload(BaseModel):
    companies: list[CompanyQuery] = Field(min_length=1)
    base_url: str = "https://www.asx.com.au/markets/trade-our-cash-market/announcements"
    output_dir: str = "downloads"
    headless: bool = True
    timeout_ms: int = 30000
    delay_seconds: float = 1.25
    max_pages: int = 1
    enforce_row_ticker_match: bool = False


class AnnouncementRecord(BaseModel):
    company_key: str
    title: str
    issuer: str | None = None
    published_date: date | None = None
    announcement_url: str | None = None
    file_urls: list[str] = Field(default_factory=list)


class DownloadResult(BaseModel):
    source_url: str
    saved_path: str | None
    status: Literal["downloaded", "skipped", "failed"]
    reason: str | None = None


class CompanyRunSummary(BaseModel):
    company_key: str
    extracted_announcements: int
    downloaded_files: int
    skipped_files: int
    failed_files: int


class RunSummary(BaseModel):
    companies: list[CompanyRunSummary]
    total_announcements: int
    total_downloaded: int
    total_skipped: int
    total_failed: int
