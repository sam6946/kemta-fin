"""Rate limiting des endpoints sensibles (OTP, reset, connexion)."""

from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle

from apps.users.services.phone import normalize_phone


def _destination_identity(request) -> str:
    data = getattr(request, "data", None) or {}
    phone = (data.get("phone") or "").strip()
    if phone:
        normalized, error = normalize_phone(phone)
        return normalized or (f"raw:{phone}" if error else phone)
    email = (data.get("email") or "").strip().lower()
    return email or ""


class DestinationRateThrottle(SimpleRateThrottle):
    """Limite par **destination** (numéro ou email) et, à défaut, par adresse IP."""

    def get_cache_key(self, request, view):
        ident = _destination_identity(request) or self.get_ident(request)
        return f"throttle_{self.scope}_{ident}"


class OTPRequestThrottle(DestinationRateThrottle):
    scope = "otp_request"


class PasswordResetThrottle(DestinationRateThrottle):
    scope = "password_reset"


class LoginThrottle(DestinationRateThrottle):
    scope = "login"
