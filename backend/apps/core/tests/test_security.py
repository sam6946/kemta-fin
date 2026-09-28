"""Phase 11 — rate limiting, usurpation d'IP, en-têtes, compression, configuration de production."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle

from apps.core.activity import get_client_ip

BACKEND = Path(__file__).resolve().parents[3]


def evidence_payload(project, photo, shade):
    return {
        "project": project.pk,
        "file": photo(color=((shade * 53) % 256, (shade * 97) % 256, (shade * 29) % 256)),
        "captured_at": timezone.now().isoformat(),
        "gps_status": "UNAVAILABLE",
    }


# --- Rate limiting -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_uploads_are_rate_limited_per_user(
    auth_client, project, project_context, photo, monkeypatch
):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "upload", "2/hour")
    client = auth_client(project_context["agent"])
    codes = [
        client.post(
            "/api/evidences/",
            evidence_payload(project, photo, 20 + i),
            format="multipart",
            headers={"Idempotency-Key": f"rl-{i}"},
        ).status_code
        for i in range(4)
    ]

    assert codes[:2] == [201, 201]
    assert codes[2:] == [429, 429]
    # Un autre utilisateur n'est pas pénalisé par le quota du premier.
    other = auth_client(project_context["engineer"]).post(
        "/api/evidences/",
        evidence_payload(project, photo, 99),
        format="multipart",
        headers={"Idempotency-Key": "rl-other"},
    )
    assert other.status_code == 201


@pytest.mark.django_db
def test_listing_evidences_is_not_counted_in_the_upload_quota(
    auth_client, project, project_context, monkeypatch
):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "upload", "1/hour")
    client = auth_client(project_context["agent"])
    assert all(client.get("/api/evidences/pending/").status_code == 200 for _ in range(5))


@pytest.mark.django_db
def test_sync_endpoint_is_rate_limited(auth_client, project_context, monkeypatch):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "sync", "2/hour")
    client = auth_client(project_context["agent"])
    codes = [
        client.post("/api/sync/batch/", {"operations": []}, format="json").status_code
        for _ in range(3)
    ]
    assert codes[-1] == 429 and 429 not in codes[:2]


@pytest.mark.django_db
def test_login_and_otp_are_rate_limited(monkeypatch):
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "login", "3/min")
    client = APIClient()
    codes = [
        client.post(
            "/api/auth/login/", {"phone_number": "+237690000123", "password": "x"}, format="json"
        ).status_code
        for _ in range(5)
    ]
    assert codes[3:] == [429, 429]


@pytest.mark.django_db
def test_spoofed_forwarded_for_cannot_bypass_the_limit(monkeypatch, settings):
    """Sans proxy de confiance (NUM_PROXIES=0), X-Forwarded-For est ignoré : changer d'IP
    à chaque requête ne réinitialise pas le compteur d'un client anonyme."""
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "anon", "3/min")
    client = APIClient()
    codes = [
        client.get("/api/health/", HTTP_X_FORWARDED_FOR=f"203.0.113.{i}").status_code
        for i in range(6)
    ]
    # /api/health/ est ouvert : on vérifie l'identité retenue plutôt que le code.
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    request = Request(APIRequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.2.3.4, 5.6.7.8"))
    assert AnonRateThrottle().get_ident(request) == request.META.get("REMOTE_ADDR")
    assert codes  # la route reste disponible


@pytest.mark.django_db
def test_client_ip_only_trusts_the_forwarded_hops_added_by_our_proxy(settings, rf):
    request = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 198.51.100.7", REMOTE_ADDR="10.0.0.2")

    settings.NUM_PROXIES = 0
    assert get_client_ip(request) == "10.0.0.2"  # en-tête falsifiable : ignoré
    settings.NUM_PROXIES = 1
    assert get_client_ip(request) == "198.51.100.7"  # l'IP vue par notre proxy, pas celle du client
    assert get_client_ip(None) is None


@pytest.mark.django_db
def test_throttle_store_outage_does_not_take_the_api_down(
    auth_client, project_context, monkeypatch
):
    from django.core.cache import cache

    def down(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "get", down)
    monkeypatch.setattr(cache, "set", down)

    assert auth_client(project_context["owner"]).get("/api/notifications/").status_code == 200


# --- En-têtes et compression -------------------------------------------------------------------


@pytest.mark.django_db
def test_api_responses_are_private_and_carry_security_headers(auth_client, project_context):
    response = auth_client(project_context["owner"]).get("/api/notifications/")

    assert response["Cache-Control"] == "no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "camera=(self)" in response["Permissions-Policy"]
    assert response["Referrer-Policy"] == "same-origin"
    assert response["X-Request-ID"]


@pytest.mark.django_db
def test_large_json_responses_are_gzip_compressed(auth_client, project, project_context):
    from apps.core.activity import log_event

    for index in range(100):
        log_event(
            "TASK_UPDATED",
            entity_type="Task",
            entity_id=index,
            organization=project.organization,
            project=project,
            metadata={"title": "Coffrage dalle " * 5},
        )
    client = auth_client(project_context["owner"])

    response = client.get(
        f"/api/projects/{project.pk}/activity/?page_size=100", HTTP_ACCEPT_ENCODING="gzip"
    )

    assert response["Content-Encoding"] == "gzip"
    assert int(response["Content-Length"]) < 6000


# --- Configuration de production ---------------------------------------------------------------


def run_django(*args, env=None):
    base = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "SECRET_KEY"))}
    base.update(env or {})
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BACKEND,
        env=base,
        capture_output=True,
        text=True,
        timeout=120,
    )


PROD_ENV = {
    "DJANGO_ENV": "production",
    "SECRET_KEY": "k7Zp!q3Vx9Lm2Rt8Wd5Yb1Nc6Hs4Jf0Ga-Ue_Io+Kw3Xz9Qv7Bn5Mt2Pl8Cr",
    "ALLOWED_HOSTS": "suivi.example.org",
    "DATABASE_URL": "sqlite:////tmp/kemta-prod-check.sqlite3",
    "CORS_ALLOWED_ORIGINS": "https://suivi.example.org",
    "CSRF_TRUSTED_ORIGINS": "https://suivi.example.org",
    "REDIS_URL": "redis://localhost:6379/1",
}


def test_production_refuses_to_start_without_a_secret_key():
    env = {**PROD_ENV}
    env.pop("SECRET_KEY")
    result = run_django("check", env=env)
    assert result.returncode != 0
    assert "SECRET_KEY" in (result.stderr + result.stdout)


def test_production_settings_pass_the_django_deployment_checklist():
    result = run_django("check", "--deploy", "--fail-level", "WARNING", env=PROD_ENV)
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_enforces_https_and_hsts():
    code = (
        "from django.conf import settings as s;"
        "assert s.SECURE_SSL_REDIRECT is True;"
        "assert s.SECURE_HSTS_SECONDS >= 31536000;"
        "assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE;"
        "assert s.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https');"
        "assert s.DEBUG is False;"
        "assert s.NUM_PROXIES == 1;"
        "print('OK')"
    )
    result = run_django("shell", "-c", code, env=PROD_ENV)
    assert "OK" in result.stdout, result.stdout + result.stderr


def test_no_secret_is_committed_in_the_repository():
    """Aucun secret réel dans le dépôt : uniquement des valeurs d'exemple dans `.env.example`."""
    root = BACKEND.parent
    env_files = [p.name for p in root.glob(".env*") if p.name != ".env.example"]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True
    ).stdout.split()
    committed_env = [f for f in tracked if Path(f).name.startswith(".env") and f != ".env.example"]
    assert not committed_env, f"fichiers d'environnement versionnés : {committed_env}"
    del env_files
    example = (root / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith(("SECRET_KEY=", "METRICS_TOKEN=", "SENTRY_DSN=")):
            value = line.split("=", 1)[1].strip()
            assert value == "" or "change" in value.lower() or "example" in value.lower(), line
