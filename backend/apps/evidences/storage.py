"""Traitement des fichiers de preuve : validation réelle, empreinte, dérivées.

Aucune confiance n'est accordée au client : le type MIME est déterminé par le **contenu**
(Pillow), la taille et les dimensions sont bornées, le chemin de stockage est régénéré et
l'empreinte SHA-256 est calculée côté serveur. Les dérivées (miniature + version liste) sont
produites hors du cycle de requête HTTP (voir `tasks.py`).
"""

from __future__ import annotations

import hashlib
import io
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

from apps.core.exceptions import KemtaAPIError

logger = logging.getLogger("kemta.evidences")

# Types acceptés : photos de terrain uniquement (pas de PDF, pas de vidéo dans le MVP).
ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
ALLOWED_CONTENT_TYPES = frozenset(ALLOWED_FORMATS.values())

# Bornes de sécurité : au-delà, la mémoire du téléphone ou du serveur souffre.
MAX_DIMENSION_PX = 4000
THUMBNAIL_SIZE = (320, 320)
LIST_SIZE = (1080, 1080)
THUMBNAIL_QUALITY = 75


def max_upload_bytes() -> int:
    return int(settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024


def sha256_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_and_validate_upload(upload) -> tuple[bytes, str]:
    """Lit le fichier, vérifie sa taille et son **type réel**, renvoie `(octets, mime)`.

    Codes d'erreur : `file_too_large` (413), `unsupported_media_type` (415),
    `image_dimensions_too_large` (400), `file_empty` (400).
    """
    limit = max_upload_bytes()
    size = getattr(upload, "size", None)
    if size is not None and size > limit:
        raise KemtaAPIError(
            "file_too_large",
            f"Le fichier dépasse la limite de {settings.MAX_UPLOAD_SIZE_MB} Mo.",
            http_status=413,
            details={"max_mb": settings.MAX_UPLOAD_SIZE_MB, "received_bytes": size},
        )

    payload = upload.read()
    if not payload:
        raise KemtaAPIError("file_empty", "Le fichier envoyé est vide.")
    if len(payload) > limit:
        raise KemtaAPIError(
            "file_too_large",
            f"Le fichier dépasse la limite de {settings.MAX_UPLOAD_SIZE_MB} Mo.",
            http_status=413,
            details={"max_mb": settings.MAX_UPLOAD_SIZE_MB, "received_bytes": len(payload)},
        )

    try:
        with Image.open(io.BytesIO(payload)) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise KemtaAPIError(
            "unsupported_media_type",
            "Format non pris en charge : envoyez une photo JPEG, PNG ou WebP.",
            http_status=415,
            details={"allowed": sorted(ALLOWED_CONTENT_TYPES)},
        ) from exc

    if image_format not in ALLOWED_FORMATS:
        raise KemtaAPIError(
            "unsupported_media_type",
            "Format non pris en charge : envoyez une photo JPEG, PNG ou WebP.",
            http_status=415,
            details={"detected": image_format, "allowed": sorted(ALLOWED_CONTENT_TYPES)},
        )

    if max(width, height) > MAX_DIMENSION_PX:
        raise KemtaAPIError(
            "image_dimensions_too_large",
            f"Image trop grande : {MAX_DIMENSION_PX} px maximum sur le plus grand côté.",
            details={"max_px": MAX_DIMENSION_PX, "width": width, "height": height},
        )

    return payload, ALLOWED_FORMATS[image_format]


def _normalized(image: Image.Image) -> Image.Image:
    """Oriente l'image selon l'EXIF puis la convertit en RVB (les téléphones tournent)."""
    oriented = ImageOps.exif_transpose(image)
    if oriented.mode not in {"RGB", "L"} or oriented.mode == "L":
        oriented = oriented.convert("RGB")
    return oriented


def _supports_webp() -> bool:
    try:
        return "WEBP" in Image.registered_extensions().values() or "WEBP" in Image.OPEN
    except Exception:  # pragma: no cover - dépend de la build Pillow
        return False


def build_derivatives(payload: bytes) -> dict[str, ContentFile]:
    """Produit la miniature (320 px) et la version liste (1080 px) depuis les octets reçus.

    Retourne des `ContentFile` nommés : l'appelant décide du stockage. En l'absence de support
    WebP dans Pillow, la miniature est écrite en JPEG — le contrat d'API n'impose pas le format,
    seule l'usage en liste compte.
    """
    with Image.open(io.BytesIO(payload)) as image:
        normalized = _normalized(image)

        thumbnail = normalized.copy()
        thumbnail.thumbnail(THUMBNAIL_SIZE, Image.LANCZOS)
        list_version = normalized.copy()
        list_version.thumbnail(LIST_SIZE, Image.LANCZOS)

        use_webp = _supports_webp()
        thumbnail_format = "WEBP" if use_webp else "JPEG"
        thumbnail_extension = "webp" if use_webp else "jpg"

        thumbnail_buffer = io.BytesIO()
        thumbnail.save(
            thumbnail_buffer,
            format=thumbnail_format,
            quality=THUMBNAIL_QUALITY,
            method=4 if use_webp else None,
            optimize=not use_webp,
        )
        list_buffer = io.BytesIO()
        list_version.save(list_buffer, format="JPEG", quality=80, optimize=True)

    return {
        "thumbnail": ContentFile(thumbnail_buffer.getvalue(), name=f"thumb.{thumbnail_extension}"),
        "list_version": ContentFile(list_buffer.getvalue(), name="list.jpg"),
    }
