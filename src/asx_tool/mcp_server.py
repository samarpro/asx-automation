from __future__ import annotations

import argparse
import asyncio
import json

from .asx_scraper import ASXScraper
from .input_loader import InputFileError, load_input_file
from .models import InputPayload


DOWNLOAD_ASX_ANNOUNCEMENTS_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "ASX ticker, e.g. BHP",
                    },
                    "company_name": {
                        "type": "string",
                        "description": "Company display name",
                    },
                    "date_from": {
                        "type": "string",
                        "format": "date",
                        "description": "Start date (inclusive), YYYY-MM-DD",
                    },
                    "date_to": {
                        "type": "string",
                        "format": "date",
                        "description": "End date (inclusive), YYYY-MM-DD",
                    },
                },
                "anyOf": [
                    {"required": ["ticker"]},
                    {"required": ["company_name"]},
                ],
                "additionalProperties": False,
            },
            "description": "List of company queries",
        },
        "base_url": {
            "type": "string",
            "default": "https://www.asx.com.au/markets/trade-our-cash-market/announcements",
        },
        "output_dir": {
            "type": "string",
            "default": "downloads",
        },
        "headless": {
            "type": "boolean",
            "default": True,
        },
        "timeout_ms": {
            "type": "integer",
            "default": 30000,
        },
        "delay_seconds": {
            "type": "number",
            "default": 1.25,
        },
        "max_pages": {
            "type": "integer",
            "default": 1,
        },
        "enforce_row_ticker_match": {
            "type": "boolean",
            "default": False,
        },
    },
    "required": ["companies"],
    "additionalProperties": False,
}


async def run_asx_download_job(input_file: str) -> dict:
    payload = load_input_file(input_file)
    summary = await ASXScraper(payload).run()
    return summary.model_dump(mode="json")


async def run_asx_download_payload(payload_data: dict) -> dict:
    payload = InputPayload.model_validate(payload_data)
    summary = await ASXScraper(payload).run()
    return summary.model_dump(mode="json")


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Download ASX announcement files by company from JSON input"
    )
    parser.add_argument("--input-file", required=True, help="Path to JSON input file")
    args = parser.parse_args()

    try:
        result = asyncio.run(run_asx_download_job(args.input_file))
        print(json.dumps({"ok": True, "result": result}, indent=2))
    except InputFileError as exc:
        print(
            json.dumps(
                {"ok": False, "error": "validation_error", "message": str(exc)},
                indent=2,
            )
        )
        raise SystemExit(2)
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": "runtime_error", "message": str(exc)},
                indent=2,
            )
        )
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download ASX announcement files by company from JSON input"
    )
    parser.add_argument("--input-file", help="Path to JSON input file")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as MCP server (stdio transport)",
    )
    args = parser.parse_args()

    if args.mcp:
        from fastmcp import FastMCP

        server = FastMCP("asx-downloader")

        @server.tool(
            name="download_asx_announcements",
            description="Downloads ASX announcement files based on company queries specified in a JSON file. Returns a summary of the download results.",
            # input_schema=DOWNLOAD_ASX_ANNOUNCEMENTS_INPUT_SCHEMA,
            timeout=1800,
        )
        async def download_asx_announcements(
            input_data: InputPayload
        ) -> dict:
            """Read company queries from JSON and download all matching ASX announcement files."""
            payload_data = {
                "companies": input_data.companies,
                "base_url": input_data.base_url,
                "output_dir": input_data.output_dir,
                "headless": input_data.headless,
                "timeout_ms": input_data.timeout_ms,
                "delay_seconds": input_data.delay_seconds,
                "max_pages": input_data.max_pages,
                "enforce_row_ticker_match": input_data.enforce_row_ticker_match,
            }
            return await run_asx_download_payload(payload_data)

        server.run()
    elif args.input_file:
        _run_cli()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
