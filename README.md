# ASX Automation

ASX Automation is a Python tool that downloads ASX announcement files for one or more companies. It supports both CLI usage and MCP tool-calling.
**This works for opencode and Github Copilot with little to no additional configurations.**

## Features

- Download ASX announcement attachments by ticker and/or company name.
- Optional date-range filtering per company.
- Configurable output directory, browser behavior, and paging.
- MCP-callable endpoint for agent/tool integrations.

## Installation

```bash
pip install -e .
playwright install chromium
```

## Run Modes

CLI mode:

```bash
asx-download --input-file examples/input.companies.json
```

MCP server mode:

```bash
asx-mcp
```

## MCP Input Schema

The MCP tool expects this top-level payload shape:

```json
{
  "input_data": {
    "companies": [
      {
        "ticker": "BHP",
        "company_name": "BHP Group Ltd",
        "date_from": "2026-01-01",
        "date_to": "2026-03-31"
      }
    ],
    "base_url": "https://www.asx.com.au/markets/trade-our-cash-market/announcements",
    "output_dir": "downloads",
    "headless": true,
    "timeout_ms": 30000,
    "delay_seconds": 1.25,
    "max_pages": 1,
    "enforce_row_ticker_match": false
  }
}
```

### Field Details

- `input_data` (object, required)
- `input_data.companies` (array, required, minimum 1)
  - `ticker` (`string | null`)
  - `company_name` (`string | null`)
  - `date_from` (`YYYY-MM-DD | null`)
  - `date_to` (`YYYY-MM-DD | null`)
- `input_data.base_url` (`string`, default: ASX announcements URL)
- `input_data.output_dir` (`string`, default: `downloads`)
- `input_data.headless` (`boolean`, default: `true`)
- `input_data.timeout_ms` (`integer`, default: `30000`)
- `input_data.delay_seconds` (`number`, default: `1.25`)
- `input_data.max_pages` (`integer`, default: `1`)
- `input_data.enforce_row_ticker_match` (`boolean`, default: `false`)

## Minimal Valid MCP Payload

```json
{
  "input_data": {
    "companies": [
      {
        "ticker": "BHP",
        "company_name": null,
        "date_from": null,
        "date_to": null
      }
    ]
  }
}
```
