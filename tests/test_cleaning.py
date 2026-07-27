"""
Unit tests for cleaning.py -- run with: pytest tests/
These don't touch monday.com or Claude; they test the deterministic
normalization logic in isolation, which is the part most worth trusting.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from monday_agent import cleaning


def test_normalize_date_handles_multiple_formats():
    assert cleaning.normalize_date("2024-03-15")[0] == "2024-03-15"
    assert cleaning.normalize_date("15/03/2024")[0] is not None
    assert cleaning.normalize_date("March 15, 2024")[0] == "2024-03-15"


def test_normalize_date_flags_empty():
    val, unparseable = cleaning.normalize_date("")
    assert val is None and unparseable is True

    val, unparseable = cleaning.normalize_date(None)
    assert val is None and unparseable is True


def test_normalize_category_fuzzy_matches():
    label, guessed = cleaning.normalize_category("energy sector", cleaning.CANONICAL_SECTORS)
    assert label == "Energy"

    label, guessed = cleaning.normalize_category("  Mining ", cleaning.CANONICAL_SECTORS)
    assert label == "Mining"


def test_normalize_category_unmapped_when_no_match():
    label, guessed = cleaning.normalize_category("xyz totally unrelated", cleaning.CANONICAL_SECTORS)
    assert label.startswith("Unmapped") or label == "Unknown"
    assert guessed is True


def test_normalize_currency_strips_symbols():
    assert cleaning.normalize_currency("$12,500.00")[0] == 12500.00
    assert cleaning.normalize_currency("₹1,00,000")[0] is not None


def test_normalize_currency_flags_missing():
    val, missing = cleaning.normalize_currency("")
    assert val is None and missing is True


def test_normalize_name_case_and_whitespace():
    assert cleaning.normalize_name("  Acme  Corp ") == "acme corp"
    assert cleaning.normalize_name("ACME CORP") == "acme corp"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
