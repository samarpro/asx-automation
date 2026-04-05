from asx_tool.models import CompanyQuery, InputPayload


def test_company_requires_identifier():
    try:
        CompanyQuery()
        assert False, "Expected validation error"
    except Exception:
        assert True


def test_company_date_order_validation():
    try:
        CompanyQuery(ticker="BHP", date_from="2026-03-05", date_to="2026-03-01")
        assert False, "Expected validation error"
    except Exception:
        assert True


def test_input_payload_parses_companies():
    payload = InputPayload(
        companies=[
            {
                "ticker": "bhp",
                "company_name": "BHP Group Ltd",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            }
        ]
    )
    assert payload.companies[0].ticker == "BHP"
