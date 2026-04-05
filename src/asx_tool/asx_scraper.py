from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx
from playwright.async_api import async_playwright

from .downloads import DownloadIndex, build_filename, save_download_content, slugify
from .models import (
    AnnouncementRecord,
    CompanyQuery,
    CompanyRunSummary,
    DownloadResult,
    InputPayload,
    RunSummary,
)


FILE_LINK_RE = re.compile(r"\\.(pdf|doc|docx|xls|xlsx|zip)$", re.IGNORECASE)
ASX_FILE_URL_RE = re.compile(r"/asx-research/1\.0/file/", re.IGNORECASE)
DATE_PATTERNS = ["%d/%m/%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"]
ASX_PREDICTIVE_API = (
    "https://asx.api.markitdigital.com/asx-research/1.0/search/predictive"
)
ASX_ANNOUNCEMENTS_API = (
    "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements"
)
ASX_FILE_BASE = (
    "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/"
)

SEARCH_INPUT_SELECTORS = [
    "input.mk-ac[name='search']:not([aria-hidden='true'])",
    "input[aria-label='Search for a company by code or name']:not([aria-hidden='true'])",
    'input[placeholder*="Search"]',
    'input[type="search"]',
    'input[name*="search"]',
    'input[name*="query"]',
]

RESULT_ROW_SELECTORS = [
    "table.table.table-bordered tbody tr",
    "table tbody tr",
    "article",
    ".announcement-item",
    "[data-testid*='announcement']",
]

NEXT_PAGE_SELECTORS = [
    "button.page-link[aria-label='Go to next page']",
    "button[aria-label='Go to next page']",
    "a[aria-label*='Next']",
    "button[aria-label*='Next']",
    "a:has-text('Next')",
    "button:has-text('Next')",
]


def is_file_link(url: str) -> bool:
    return bool(FILE_LINK_RE.search(url) or ASX_FILE_URL_RE.search(url))


class ASXScraper:
    def __init__(self, payload: InputPayload) -> None:
        self.payload = payload
        self.output_root = Path(payload.output_dir)

    async def run(self) -> RunSummary:
        company_summaries: list[CompanyRunSummary] = []

        print(f"Starting ASX scraper for {len(self.payload.companies)} companies...")
        print(f"Output directory: {self.output_root}")

        try:
            async with async_playwright() as p:
                for idx, company in enumerate(self.payload.companies, start=1):
                    company_key = company.ticker or company.company_name or "unknown"
                    print(
                        f"\n[{idx}/{len(self.payload.companies)}] Processing company: {company_key}"
                    )

                    # Launch a fresh browser for each company to avoid long-running process issues
                    print("  Launching browser...")
                    browser = await p.chromium.launch(headless=self.payload.headless)
                    context = await browser.new_context(accept_downloads=True)
                    page = await context.new_page()
                    page.set_default_timeout(self.payload.timeout_ms)

                    try:
                        try:
                            announcements = (
                                await self._collect_announcements_for_company(
                                    page, company
                                )
                            )
                        except Exception as scrape_error:
                            print(
                                f"  ⚠ Warning: Web scraping failed ({type(scrape_error).__name__}), trying API fallback..."
                            )
                            announcements = await self._fetch_announcements_via_api(
                                company
                            )

                        print(f"  → Collected {len(announcements)} announcements")

                        downloads = await self._download_files_for_company(
                            company, announcements
                        )

                        downloaded = sum(
                            1 for d in downloads if d.status == "downloaded"
                        )
                        skipped = sum(1 for d in downloads if d.status == "skipped")
                        failed = sum(1 for d in downloads if d.status == "failed")

                        print(
                            f"  → Downloaded: {downloaded}, Skipped: {skipped}, Failed: {failed}"
                        )

                        company_summaries.append(
                            CompanyRunSummary(
                                company_key=company_key,
                                extracted_announcements=len(announcements),
                                downloaded_files=downloaded,
                                skipped_files=skipped,
                                failed_files=failed,
                            )
                        )
                    except Exception as company_error:
                        print(
                            f"  ✗ Error processing {company_key}: {type(company_error).__name__}: {company_error}"
                        )
                        company_summaries.append(
                            CompanyRunSummary(
                                company_key=company_key,
                                extracted_announcements=0,
                                downloaded_files=0,
                                skipped_files=0,
                                failed_files=0,
                            )
                        )
                    finally:
                        print("  Closing browser...")
                        try:
                            await browser.close()
                        except Exception:
                            pass  # Browser may already be closed or crashed

        except Exception as e:
            print(f"ERROR during browser automation: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            raise

        total_announcements = sum(c.extracted_announcements for c in company_summaries)
        total_downloaded = sum(c.downloaded_files for c in company_summaries)
        total_skipped = sum(c.skipped_files for c in company_summaries)
        total_failed = sum(c.failed_files for c in company_summaries)

        print(f"\n{'=' * 60}")
        print(
            f"SUMMARY: {total_announcements} announcements | {total_downloaded} downloaded | {total_skipped} skipped | {total_failed} failed"
        )
        print(f"{'=' * 60}\n")

        return RunSummary(
            companies=company_summaries,
            total_announcements=total_announcements,
            total_downloaded=total_downloaded,
            total_skipped=total_skipped,
            total_failed=total_failed,
        )

    async def _collect_announcements_for_company(
        self, page, company: CompanyQuery
    ) -> list[AnnouncementRecord]:
        is_tickered_url = await self._navigate_to_announcements(page, company)

        # Only search if we're on the base URL (not a ticker-specific URL)
        if not is_tickered_url:
            # these are only valid for base URL, ticker-specific pages are already filtered by ticker and don't have a search box
            await self._search_company(page, company)
            await self._apply_date_inputs_if_available(page, company)

        company_key = company.ticker or company.company_name or "unknown"
        extracted: list[AnnouncementRecord] = []
        seen_keys: set[str] = set()

        for page_num in range(self.payload.max_pages):
            rows = await self._row_locators(page)
            print(f"    Page {page_num + 1}: Found {len(rows)} rows")

            for row in rows:
                item = await self._parse_row(row, company_key)
                if not item:
                    continue
                print(
                    f"      Parsed row: {item.title[:60]} | Date: {item.published_date} | Files: {len(item.file_urls)} | URL: {item.announcement_url}"
                )

                # Skip row matching check if using ticker-specific URL (already filtered by ticker)
                if self.payload.enforce_row_ticker_match and not is_tickered_url:
                    if not await self._row_matches_company(row, company):
                        row_text = (await row.inner_text()).strip()[:100]
                        print(f"      Skipping non-matching row: {row_text}...")
                        continue

                dedupe_key = (
                    f"{item.title}|{item.published_date}|{item.announcement_url}"
                )
                if dedupe_key in seen_keys:
                    continue

                if self._within_date_range(item, company):
                    extracted.append(item)
                    seen_keys.add(dedupe_key)

            if not await self._go_to_next_page(page):
                print(f"    No more pages after page {page_num + 1}")
                break

            await asyncio.sleep(self.payload.delay_seconds)

        # Populate file links from detail pages when missing on list rows.
        for record in extracted:
            if record.file_urls or not record.announcement_url:
                continue
            record.file_urls = await self._extract_file_links_from_detail(
                record.announcement_url
            )

        # Fallback to direct API retrieval when the UI layer yields no rows.
        if not extracted:
            print("    No announcements found via UI, trying API fallback...")
            extracted = await self._fetch_announcements_via_api(company)

        return extracted

    async def _navigate_to_announcements(self, page, company: CompanyQuery) -> bool:
        """Navigate to announcements page. Returns True if using ticker-specific URL."""
        target_url = self.payload.base_url
        is_tickered_url = False

        if company.ticker:
            target_url = f"{self.payload.base_url}.{company.ticker.lower()}"
            is_tickered_url = True

        await page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=self.payload.timeout_ms,
        )

        # Ticker-specific URLs don't have a search bar, so wait for table to load instead
        if is_tickered_url:
            try:
                await page.wait_for_selector(
                    "table.table.table-bordered tbody tr, table tbody tr",
                    state="visible",
                    timeout=self.payload.timeout_ms,
                )

                # Wait for the table to be populated with real data (not just dummy rows)
                # The table initially loads with 3 placeholder rows while API is being called
                print("  Table found, waiting for data to populate...")

                # Strategy: Wait for the row count to stabilize above the dummy threshold
                max_wait_iterations = 10
                stable_count_threshold = 2  # Number of consecutive same counts needed

                previous_count = 0
                stable_iterations = 0

                for _ in range(max_wait_iterations):
                    await asyncio.sleep(0.5)  # Check every 500ms

                    rows = await page.locator(
                        "table.table.table-bordered tbody tr, table tbody tr"
                    ).count()

                    # If we have more than 3 rows and count is stable, we have real data
                    if rows > 3:
                        if rows == previous_count:
                            stable_iterations += 1
                            if stable_iterations >= stable_count_threshold:
                                print(f"  Table populated with {rows} rows")
                                break
                        else:
                            stable_iterations = 0
                        previous_count = rows
                    elif rows == 3:
                        # Still on dummy rows, keep waiting
                        previous_count = rows
                    else:
                        # Less than 3 rows might mean no announcements
                        print(f"  Table has {rows} rows (might be empty)")
                        break
                else:
                    # Timeout reached, proceed anyway
                    final_count = await page.locator(
                        "table.table.table-bordered tbody tr, table tbody tr"
                    ).count()
                    print(
                        f"  Table population wait timeout, proceeding with {final_count} rows"
                    )

            except Exception:
                # Table might not appear if there are no announcements
                print(
                    "  No announcement table found on ticker-specific URL, proceeding without search..."
                )
                pass
        else:
            await page.wait_for_selector(
                "input.mk-ac[name='search']:not([aria-hidden='true']), input[aria-label='Search for a company by code or name']:not([aria-hidden='true'])",
                state="visible",
                timeout=self.payload.timeout_ms,
            )
        print(f"  Navigated to: {target_url}")
        return is_tickered_url

    async def _row_matches_company(self, row, company: CompanyQuery) -> bool:
        row_text = (await row.inner_text()).upper()

        if company.ticker and company.ticker.upper() not in row_text:
            return False

        if company.company_name:
            normalized_name = company.company_name.upper()
            if normalized_name not in row_text and not company.ticker:
                return False

        return True

    async def _search_company(self, page, company: CompanyQuery) -> None:
        query = company.ticker or company.company_name
        if not query:
            return

        # Check if search input exists (it won't on ticker-specific URLs)
        for selector in SEARCH_INPUT_SELECTORS:
            input_box = page.locator(f"{selector}:visible").first
            if await input_box.count() == 0:
                continue

            print(f"  Searching for: {query}")
            await input_box.fill(query)
            await input_box.press("Enter")
            await asyncio.sleep(self.payload.delay_seconds)

            # Prefer selecting an exact autocomplete option to force entity filtering.
            options = page.locator("ul.mk-ac-list [role='option']")
            options_count = await options.count()

            if options_count > 0:
                print(f"  Found {options_count} autocomplete options")

                if company.ticker:
                    ticker_option = options.filter(has_text=company.ticker.upper())
                    if await ticker_option.count() > 0:
                        print(f"  Selecting ticker option: {company.ticker.upper()}")
                        await ticker_option.first.click()
                        await asyncio.sleep(self.payload.delay_seconds)
                        return

                if company.company_name:
                    company_option = options.filter(has_text=company.company_name)
                    if await company_option.count() > 0:
                        print(f"  Selecting company option: {company.company_name}")
                        await company_option.first.click()
                        await asyncio.sleep(self.payload.delay_seconds)
                        return

                print(f"  No exact match found, selecting first option")
                await options.first.click()
                await asyncio.sleep(self.payload.delay_seconds)
            else:
                print(
                    f"  No autocomplete options appeared, search may have filtered directly"
                )
            return

    async def _apply_date_inputs_if_available(
        self, page, company: CompanyQuery
    ) -> None:
        # ASX uses a custom day/month picker with dynamic IDs.
        # Keep date filtering deterministic by applying post-extraction filtering.
        _ = (page, company)

    async def _row_locators(self, page):
        print("      Locating announcement rows...")
        for selector in RESULT_ROW_SELECTORS:
            print(f"      Trying selector: {selector}")
            locator = page.locator(selector)
            count = await locator.count()
            if count > 0:
                print(f"      Using selector: {selector} (found {count} rows)")
                return [locator.nth(i) for i in range(count)]
        print(f"      No rows found with any selector")
        return []

    async def _parse_row(self, row, company_key: str) -> AnnouncementRecord | None:
        text = (await row.inner_text()).strip()
        if not text:
            return None

        cells = row.locator("td")
        print(cells)
        cells_count = await cells.count()
        date_text = (
            (await cells.nth(0).inner_text()).strip() if cells_count > 0 else text
        )
        title_text = (
            (await cells.nth(5).inner_text()).strip() if cells_count > 5 else text
        )

        links = row.locator("a")
        hrefs: list[str] = []
        links_count = await links.count()
        for i in range(links_count):
            href = await links.nth(i).get_attribute("href")
            if href:
                hrefs.append(urljoin(self.payload.base_url, href))

        title = await self._extract_title(row, title_text)
        published_date = self._extract_date(date_text)

        file_urls = [h for h in hrefs if is_file_link(h)]
        announcement_url = next((h for h in hrefs if not is_file_link(h)), None)

        return AnnouncementRecord(
            company_key=company_key,
            title=title,
            issuer=None,
            published_date=published_date,
            announcement_url=announcement_url,
            file_urls=file_urls,
        )

    async def _extract_title(self, row, fallback_text: str) -> str:
        links = row.locator("td a, a")
        if await links.count() > 0:
            candidate = (await links.first.inner_text()).strip()
            if candidate:
                return candidate.replace("opens new window", "").strip()
        cleaned = fallback_text.replace("opens new window", "").strip()
        if not cleaned:
            return "announcement"
        return cleaned.splitlines()[0][:180]

    def _extract_date(self, text: str):
        token_candidates = re.findall(
            r"\\b\\d{1,2}/\\d{1,2}/\\d{4}\\b|\\b\\d{4}-\\d{2}-\\d{2}\\b|\\b\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4}\\b",
            text,
        )
        for token in token_candidates:
            for pattern in DATE_PATTERNS:
                try:
                    return datetime.strptime(token, pattern).date()
                except ValueError:
                    pass
        return None

    def _within_date_range(
        self, item: AnnouncementRecord, company: CompanyQuery
    ) -> bool:
        if not item.published_date:
            return True
        if company.date_from and item.published_date < company.date_from:
            return False
        if company.date_to and item.published_date > company.date_to:
            return False
        return True

    async def _go_to_next_page(self, page) -> bool:
        for selector in NEXT_PAGE_SELECTORS:
            button = page.locator(selector).first
            if await button.count() == 0:
                continue

            if await button.get_attribute("disabled") is not None:
                return False
            if await button.get_attribute("aria-disabled") == "true":
                return False

            try:
                await button.click()
            except Exception:
                # Consent overlays can intercept pointer events; force click to continue pagination.
                await button.click(force=True)
            await page.wait_for_timeout(
                int(max(self.payload.delay_seconds, 0.5) * 1000)
            )
            return True
        return False

    async def _extract_file_links_from_detail(self, detail_url: str) -> list[str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(detail_url, timeout=30)
                response.raise_for_status()
        except httpx.HTTPError:
            return []

        hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", response.text)
        urls = [urljoin(detail_url, h) for h in hrefs]
        return [u for u in urls if is_file_link(u)]

    async def _fetch_announcements_via_api(
        self, company: CompanyQuery
    ) -> list[AnnouncementRecord]:
        if not company.ticker:
            return []

        entity_xid = await self._get_entity_xid(company.ticker)
        if not entity_xid:
            return []

        company_key = company.ticker or company.company_name or "unknown"
        extracted: list[AnnouncementRecord] = []

        async with httpx.AsyncClient() as client:
            for page_num in range(self.payload.max_pages):
                params = {
                    "entityXids": entity_xid,
                    "page": page_num,
                    "itemsPerPage": 25,
                }
                try:
                    response = await client.get(
                        ASX_ANNOUNCEMENTS_API, params=params, timeout=45
                    )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError):
                    break

                items = payload.get("data", {}).get("items", [])
                if not items:
                    break

                for item in items:
                    published_date = None
                    iso_date = item.get("date")
                    if isinstance(iso_date, str) and iso_date:
                        try:
                            published_date = datetime.fromisoformat(
                                iso_date.replace("Z", "+00:00")
                            ).date()
                        except ValueError:
                            published_date = None

                    record = AnnouncementRecord(
                        company_key=company_key,
                        title=(item.get("headline") or "announcement").strip(),
                        issuer=(item.get("companyInfo") or [{}])[0].get("displayName"),
                        published_date=published_date,
                        announcement_url=None,
                        file_urls=[],
                    )

                    document_key = item.get("documentKey")
                    if document_key:
                        record.file_urls = [
                            f"{ASX_FILE_BASE}{document_key}&v=undefined"
                        ]

                    if self._within_date_range(record, company):
                        extracted.append(record)

        return extracted

    async def _get_entity_xid(self, ticker: str) -> int | None:
        params = {"searchText": ticker}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    ASX_PREDICTIVE_API, params=params, timeout=30
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        items = payload.get("data", {}).get("items", [])
        for item in items:
            if str(item.get("symbol", "")).upper() == ticker.upper():
                entity_xid = item.get("xidEntity")
                if isinstance(entity_xid, int):
                    return entity_xid
                if isinstance(entity_xid, str) and entity_xid.isdigit():
                    return int(entity_xid)
        return None

    async def _download_files_for_company(
        self,
        company: CompanyQuery,
        announcements: list[AnnouncementRecord],
    ) -> list[DownloadResult]:
        company_key = company.ticker or company.company_name or "unknown"
        company_dir = self.output_root / slugify(company_key)
        index = DownloadIndex(company_dir)
        results: list[DownloadResult] = []

        total_files = sum(len(a.file_urls) for a in announcements)
        file_counter = 0

        async with httpx.AsyncClient() as client:
            for announcement in announcements:
                date_prefix = (
                    announcement.published_date.isoformat()
                    if announcement.published_date
                    else None
                )
                for i, file_url in enumerate(announcement.file_urls, start=1):
                    file_counter += 1
                    try:
                        print(
                            f"    [{file_counter}/{total_files}] Downloading: {announcement.title[:60]}..."
                        )
                        response = await client.get(file_url, timeout=45)
                        response.raise_for_status()
                        file_name = build_filename(
                            date_prefix,
                            announcement.title,
                            file_url,
                            i,
                            response_headers={
                                k.lower(): v for k, v in response.headers.items()
                            },
                            content=response.content,
                        )
                        status, saved_path = save_download_content(
                            index, file_url, response.content, company_dir, file_name
                        )
                        results.append(
                            DownloadResult(
                                source_url=file_url,
                                saved_path=saved_path,
                                status=status,
                            )
                        )
                        print(f"      ✓ {status}: {Path(saved_path).name}")
                    except httpx.HTTPError as exc:
                        results.append(
                            DownloadResult(
                                source_url=file_url,
                                saved_path=None,
                                status="failed",
                                reason=str(exc),
                            )
                        )
                        print(f"      ✗ failed: {exc}")

                    await asyncio.sleep(self.payload.delay_seconds)

        return results
