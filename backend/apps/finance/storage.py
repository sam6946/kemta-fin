"""Justificatifs de dépense (factures, reçus) : validation réelle du contenu.

Comme pour les preuves terrain, **aucune confiance n'est accordée au client** : le type est
déterminé par les octets du fichier (signatures), la taille est bornée, le chemin est régénéré
et l'empreinte SHA-256 est calculée côté serveur. Différence assumée : une facture peut être une
**photo** ou un **PDF** (les fournisseurs camerounais envoient les deux).
"""

from __future__ import annotations

from django.conf import settings

from apps.core.exceptions import KemtaAPIError
from apps.evidences.storage import sha256_of  # même empreinte que les preuves (unique outil)

# Signatures binaires reconnues → type MIME réel.
MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
)
ALLOWED_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "application/pdf"})
EXTENSION_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def max_upload_bytes() -> int:
    return int(settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024


def _detect_content_type(payload: bytes) -> str | None:
    for signature, content_type in MAGIC_SIGNATURES:
        if payload.startswith(signature):
            return content_type
    # WebP : conteneur RIFF dont les octets 8-11 valent « WEBP ».
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def read_and_validate_receipt(upload) -> tuple[bytes, str]:
    """Renvoie `(octets, mime réel)` ou lève une erreur métier explicite."""
    limit = max_upload_bytes()
    size = getattr(upload, "size", None)
    if size is not None and size > limit:
        raise KemtaAPIError(
            "file_too_large",
            f"Le justificatif dépasse la limite de {settings.MAX_UPLOAD_SIZE_MB} Mo.",
            http_status=413,
            details={"max_mb": settings.MAX_UPLOAD_SIZE_MB, "received_bytes": size},
        )

    payload = upload.read()
    if not payload:
        raise KemtaAPIError("file_empty", "Le fichier envoyé est vide.")

    content_type = _detect_content_type(payload)
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise KemtaAPIError(
            "unsupported_media_type",
            "Justificatif non pris en charge : envoyez une photo (JPEG, PNG, WebP) ou un PDF.",
            http_status=415,
            details={"allowed": sorted(ALLOWED_CONTENT_TYPES)},
        )
    return payload, content_type


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "EXTENSION_BY_CONTENT_TYPE",
    "read_and_validate_receipt",
    "sha256_of",
]
