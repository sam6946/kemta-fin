"""Normalisation et validation des numéros de téléphone (identifiant principal)."""

from __future__ import annotations

import re

import phonenumbers
from django.conf import settings

PHONE_IN_ERROR = "phone_invalid"
PHONE_REGION_ERROR = "phone_region_not_supported"


def normalize_phone(raw: str, default_region: str | None = None) -> tuple[str | None, str | None]:
    """Retourne `(numéro_E164, code_erreur)`. Une seule des deux valeurs est renseignée."""
    value = (raw or "").strip()
    if not value:
        return None, PHONE_IN_ERROR

    # Saisies courantes : espaces, points, tirets, parenthèses.
    cleaned = re.sub(r"[^\d+]", "", value)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    elif not cleaned.startswith("+"):
        # Format national avec préfixe « 0 » (ex. « 06 90 12 34 56 » au Cameroun).
        cleaned = cleaned.lstrip("0") or cleaned

    region = default_region or (
        settings.PHONE_ALLOWED_REGIONS[0] if settings.PHONE_ALLOWED_REGIONS else "CM"
    )
    try:
        parsed = phonenumbers.parse(cleaned, region)
    except phonenumbers.NumberParseException:
        return None, PHONE_IN_ERROR

    if not phonenumbers.is_valid_number(parsed):
        return None, PHONE_IN_ERROR

    country = phonenumbers.region_code_for_number(parsed)
    allowed = settings.PHONE_ALLOWED_REGIONS
    if allowed and country not in allowed:
        return None, PHONE_REGION_ERROR

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164), None


def mask_phone(phone: str) -> str:
    """Masque un numéro pour l'affichage : `+237 6•• •• •• 56`."""
    digits = re.sub(r"[^\d]", "", phone or "")
    if len(digits) < 6:
        return "••••"
    tail = digits[-2:]
    head_length = max(len(digits) - 4, 3)
    hidden = "•" * max(len(digits) - head_length - 2, 2)
    return f"+{digits[:head_length]} {hidden} {tail}"
