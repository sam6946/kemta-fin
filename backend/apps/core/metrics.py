"""Métriques applicatives (phase 11) : compteurs et histogrammes au format Prometheus.

Deux moteurs, choisis par `METRICS_BACKEND` :

* `redis`  — un hash Redis partagé par tous les workers Gunicorn/Celery (production) ;
* `memory` — dictionnaire du processus (tests, développement sans Redis).

Règles de conception :

* **la mesure ne casse jamais une requête** : toute panne du moteur est avalée et journalisée
  (une seule fois par minute) ;
* **cardinalité maîtrisée** : les libellés sont des motifs de route (`/api/projects/<int:pk>/`),
  des types d'opération, des codes d'erreur — jamais des identifiants ni des numéros de téléphone ;
* aucun secret ni donnée personnelle n'est écrit dans une métrique.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

from django.conf import settings

logger = logging.getLogger("kemta.metrics")

# Durées (secondes) : couvre une réponse de cache (5 ms) jusqu'à un envoi de photo en 3G (10 s).
HISTOGRAM_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HELP = {
    "kemta_http_requests_total": "Requêtes HTTP traitées, par méthode, route et statut.",
    "kemta_http_request_duration_seconds": "Durée de traitement des requêtes HTTP.",
    "kemta_http_errors_total": "Réponses HTTP 5xx, par route.",
    "kemta_uploads_total": "Envois de fichiers (preuves, justificatifs) par issue.",
    "kemta_upload_bytes_total": "Octets acceptés par type d'envoi.",
    "kemta_sync_operations_total": "Opérations de synchronisation hors ligne par statut.",
    "kemta_sync_errors_total": "Opérations de synchronisation en conflit ou en échec, par code.",
    "kemta_celery_tasks_total": "Tâches Celery par nom et état final.",
    "kemta_celery_task_duration_seconds": "Durée d'exécution des tâches Celery.",
    "kemta_domain_events_total": "Événements métier émis, par type.",
    "kemta_notifications_total": "Notifications créées ou regroupées, par événement.",
    "kemta_rate_limited_total": "Requêtes refusées par le rate limiting, par portée.",
}


def _sample_key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    rendered = ",".join(
        f'{key}="{str(value).replace(chr(92), "").replace(chr(34), "")}"'
        for key, value in sorted(labels.items())
    )
    return f"{name}{{{rendered}}}"


class MemoryBackend:
    def __init__(self) -> None:
        self._values: dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def add(self, key: str, value: float) -> None:
        with self._lock:
            self._values[key] += value

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._values)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


class RedisBackend:
    HASH_KEY = "kemta:metrics"

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                self._url, socket_connect_timeout=1, socket_timeout=1, decode_responses=True
            )
        return self._client

    def add(self, key: str, value: float) -> None:
        self.client.hincrbyfloat(self.HASH_KEY, key, value)

    def snapshot(self) -> dict[str, float]:
        return {key: float(value) for key, value in self.client.hgetall(self.HASH_KEY).items()}

    def reset(self) -> None:
        self.client.delete(self.HASH_KEY)


_backend = None
_backend_lock = threading.Lock()
_last_error_log = 0.0


def get_backend():
    global _backend
    if _backend is None:
        with _backend_lock:
            if _backend is None:
                if getattr(settings, "METRICS_BACKEND", "memory") == "redis":
                    _backend = RedisBackend(settings.REDIS_URL)
                else:
                    _backend = MemoryBackend()
    return _backend


def _safe(action, *args):
    """Exécute une écriture de métrique sans jamais propager d'erreur."""
    global _last_error_log
    try:
        return action(*args)
    except Exception as exc:  # panne Redis, etc. : la mesure ne doit rien casser
        now = time.monotonic()
        if now - _last_error_log > 60:
            _last_error_log = now
            logger.warning("Métriques indisponibles : %s", exc.__class__.__name__)
        return None


def incr(name: str, value: float = 1.0, **labels: str) -> None:
    """Incrémente un compteur."""
    _safe(get_backend().add, _sample_key(name, labels), value)


def observe(name: str, seconds: float, **labels: str) -> None:
    """Enregistre une durée dans un histogramme (`_bucket`, `_sum`, `_count`)."""
    backend = get_backend()
    for bound in HISTOGRAM_BUCKETS:
        if seconds <= bound:
            _safe(backend.add, _sample_key(f"{name}_bucket", {**labels, "le": str(bound)}), 1)
    _safe(backend.add, _sample_key(f"{name}_bucket", {**labels, "le": "+Inf"}), 1)
    _safe(backend.add, _sample_key(f"{name}_sum", labels), seconds)
    _safe(backend.add, _sample_key(f"{name}_count", labels), 1)


def record_upload(kind: str, outcome: str, *, reason: str = "", size: int = 0) -> None:
    """Suivi des envois de fichiers (`kind` : evidence | receipt ; `outcome` : accepted,
    replayed, duplicate, rejected). Les rejets portent le **code d'erreur** (pas le message)."""
    labels = {"kind": kind, "outcome": outcome}
    if reason:
        labels["reason"] = reason
    incr("kemta_uploads_total", **labels)
    if outcome == "accepted" and size:
        incr("kemta_upload_bytes_total", size, kind=kind)


def record_sync(
    operation_type: str, status: str, *, replayed: bool = False, code: str = ""
) -> None:
    """Suivi des opérations de synchronisation hors ligne (SYNCED, CONFLICT, FAILED)."""
    incr("kemta_sync_operations_total", type=operation_type, status=status.lower())
    if replayed:
        incr("kemta_sync_operations_total", type=operation_type, status="replayed")
    if status.upper() in {"CONFLICT", "FAILED"}:
        incr("kemta_sync_errors_total", type=operation_type, code=code or "unknown")


def snapshot() -> dict[str, float]:
    """Toutes les valeurs courantes ; vide si le moteur est indisponible."""
    return _safe(get_backend().snapshot) or {}


def reset() -> None:
    _safe(get_backend().reset)


def reset_backend() -> None:
    """Oublie le moteur choisi (les tests changent de configuration)."""
    global _backend
    _backend = None


def _family(sample: str) -> str:
    base = sample.split("{", 1)[0]
    for suffix in ("_bucket", "_sum", "_count"):
        if base.endswith(suffix) and base[: -len(suffix)] in HELP:
            return base[: -len(suffix)]
    return base


def render_prometheus() -> str:
    """Format texte d'exposition Prometheus (`text/plain; version=0.0.4`)."""
    values = snapshot()
    families: dict[str, list[str]] = defaultdict(list)
    for sample in sorted(values):
        families[_family(sample)].append(sample)

    lines: list[str] = []
    for family in sorted(families):
        kind = (
            "histogram"
            if family.endswith("_seconds")
            else ("counter" if family.endswith("_total") else "gauge")
        )
        lines.append(f"# HELP {family} {HELP.get(family, family)}")
        lines.append(f"# TYPE {family} {kind}")
        for sample in families[family]:
            value = values[sample]
            rendered = str(int(value)) if float(value).is_integer() else f"{value:.6f}"
            lines.append(f"{sample} {rendered}")
    return "\n".join(lines) + "\n"


def summary() -> dict:
    """Synthèse JSON pour l'écran d'exploitation : volumes, erreurs, latence moyenne."""
    values = snapshot()
    requests = sum(v for k, v in values.items() if k.startswith("kemta_http_requests_total"))
    errors = sum(v for k, v in values.items() if k.startswith("kemta_http_errors_total"))
    duration_sum = sum(
        v for k, v in values.items() if k.startswith("kemta_http_request_duration_seconds_sum")
    )
    duration_count = sum(
        v for k, v in values.items() if k.startswith("kemta_http_request_duration_seconds_count")
    )

    def _total(prefix: str) -> int:
        return int(sum(v for k, v in values.items() if k.startswith(prefix)))

    return {
        "http_requests": int(requests),
        "http_errors_5xx": int(errors),
        "http_average_ms": round(duration_sum / duration_count * 1000, 1) if duration_count else 0,
        "uploads": _total("kemta_uploads_total"),
        "uploads_rejected": int(
            sum(
                v
                for k, v in values.items()
                if k.startswith("kemta_uploads_total") and 'outcome="rejected"' in k
            )
        ),
        "sync_operations": _total("kemta_sync_operations_total"),
        "sync_errors": _total("kemta_sync_errors_total"),
        "celery_tasks": _total("kemta_celery_tasks_total"),
        "rate_limited": _total("kemta_rate_limited_total"),
    }
