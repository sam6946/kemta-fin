"""Réglages dédiés aux tests : aucune infrastructure externe requise."""

import tempfile
from pathlib import Path

from .settings import *

ENV = "test"
DEBUG = False
DJANGO_ENV = "test"

SECRET_KEY = "test-only-insecure-key"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "kemta-pytest",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

SMS_PROVIDER = "console"

# Limite d'envoi volontairement basse en test : un seul mégaoctet suffit à vérifier le refus.
MAX_UPLOAD_SIZE_MB = 1

# Les fichiers envoyés par les tests (preuves, justificatifs) ne doivent jamais atterrir
# dans `var/media`, qui contient les médias réels de développement.
MEDIA_ROOT = str(Path(tempfile.gettempdir()) / "kemta-suivi-tests-media")

# Les tests exercent explicitement les outils de développement (boîte SMS/email) ;
# la production les désactive par défaut (voir `config/settings.py`).
ENABLE_DEV_OUTBOX = True

# Les mots de passe de test restent valides vis-à-vis des validateurs.
PASSWORD_MIN_LENGTH = 10

# Accélération des tests (les hashes sont volontairement rapides en test).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
