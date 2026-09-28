"""Fixtures locales : photos générées en mémoire, aucun fichier binaire dans le dépôt."""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image


def make_image(
    *,
    width: int = 1200,
    height: int = 900,
    image_format: str = "JPEG",
    color: tuple[int, int, int] = (30, 90, 140),
    exif_orientation: int | None = None,
) -> bytes:
    """Construit une vraie image en mémoire (JPEG/PNG/WebP) pour les tests d'upload."""
    image = Image.new("RGB", (width, height), color)
    # Un motif simple évite une image uniformément compressible (taille réaliste).
    for x in range(0, width, 40):
        for y in range(0, height, 40):
            image.putpixel((x, y), (255, 255, 255))
    buffer = io.BytesIO()
    kwargs = {}
    if exif_orientation is not None and image_format == "JPEG":
        exif = Image.Exif()
        exif[274] = exif_orientation
        kwargs["exif"] = exif
    image.save(buffer, format=image_format, **kwargs)
    return buffer.getvalue()


@pytest.fixture()
def photo():
    """Fabrique d'upload : `photo()` → JPEG 1200×900."""

    def _photo(**kwargs) -> SimpleUploadedFile:
        image_format = kwargs.get("image_format", "JPEG")
        payload = make_image(**kwargs)
        extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[image_format]
        return SimpleUploadedFile(
            f"chantier.{extension}", payload, content_type=f"image/{extension}"
        )

    return _photo


@pytest.fixture()
def capture_payload(project, photo):
    """Charge utile minimale d'un dépôt de preuve réussi."""

    def _payload(**overrides):
        payload = {
            "project": project.pk,
            "file": photo(),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        }
        payload.update(overrides)
        return payload

    return _payload
