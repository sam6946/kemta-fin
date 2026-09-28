"""Phase 1 — `/api/health/` doit vérifier l'application, la base et le cache."""

import pytest


@pytest.mark.django_db
def test_health_ok(api):
    response = api.get("/api/health/")
    assert response.status_code == 200
    data = response.data
    assert data["status"] == "ok"
    assert data["app"] == "ok"
    assert data["database"] == "ok"
    assert data["redis"] == "ok"
    assert "checks_ms" in data
    assert "version" in data and "environment" in data


@pytest.mark.django_db
def test_health_does_not_leak_secrets(api, settings):
    settings.SECRET_KEY = "super-secret-value"
    response = api.get("/api/health/")
    assert "super-secret-value" not in response.content.decode()


@pytest.mark.django_db
def test_health_degraded_when_database_down(api, monkeypatch):
    from apps.core import views as health_views

    monkeypatch.setattr(health_views, "check_database", lambda: (False, 1))
    response = api.get("/api/health/")
    assert response.status_code == 503
    assert response.data["status"] == "degraded"
    assert response.data["database"] == "down"


@pytest.mark.django_db
def test_health_degraded_when_cache_down(api, monkeypatch):
    from apps.core import views as health_views

    monkeypatch.setattr(health_views, "check_redis", lambda: (False, 1))
    response = api.get("/api/health/")
    assert response.status_code == 503
    assert response.data["redis"] == "down"


@pytest.mark.django_db
def test_health_route_is_reachable_without_auth(api):
    """Le healthcheck est public mais n'expose aucune donnée métier."""
    response = api.get("/api/health/")
    assert response.status_code in (200, 503)
    assert "users" not in response.data
