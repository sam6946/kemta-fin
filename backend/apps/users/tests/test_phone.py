"""Normalisation des numéros camerounais (identifiant principal)."""

import pytest

from apps.users.services.phone import mask_phone, normalize_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+237690123456", "+237690123456"),
        ("690123456", "+237690123456"),
        ("06 90 12 34 56", "+237690123456"),
        ("06-90-12-34-56", "+237690123456"),
        ("00237 690 123 456", "+237690123456"),
        ("+237 6 90 12 34 56", "+237690123456"),
    ],
)
def test_cameroonian_numbers_are_normalized(raw, expected):
    assert normalize_phone(raw) == (expected, None)


@pytest.mark.parametrize("raw", ["", "123", "abc", "06 90", "+237690"])
def test_invalid_numbers_are_rejected(raw):
    value, error = normalize_phone(raw)
    assert value is None
    assert error == "phone_invalid"


def test_foreign_number_is_rejected_with_dedicated_code():
    value, error = normalize_phone("+33612345678", default_region="CM")
    assert value is None
    assert error == "phone_region_not_supported"


def test_mask_phone_hides_most_digits():
    masked = mask_phone("+237690123456")
    assert masked.startswith("+237")
    assert masked.endswith("56")
    assert "6901234" not in masked
    assert "•" in masked
