"""Phase 10/11 — métriques Prometheus, middleware de mesure, état d'exploitation."""

from __future__ import annotations

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core import metrics
from apps.core.models import TaskRun
from apps.users.roles import Role

METRICS = "/api/ops/metrics/"
STATUS = "/api/ops/status/"


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset_backend()
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture()
def admin(make_user):
    return make_user(Role.PLATFORM_ADMIN, phone_number="+237699000002")


def scrape(token="test-metrics-token"):
    client = APIClient()
    return client.get(METRICS, HTTP_AUTHORIZATION=f"Bearer {token}")


def sample(text: str, prefix: str) -> float:
    for line in text.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"échantillon absent : {prefix}\n{text}")


# --- Accès ------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_metrics_reachable_with_the_scraper_token_only():
    ok = scrape()
    assert ok.status_code == 200
    assert ok["Content-Type"].startswith("text/plain")
    assert scrape("mauvais-jeton").status_code == 404
    assert APIClient().get(METRICS).status_code == 404


@pytest.mark.django_db
def test_metrics_reachable_by_platform_admin_but_not_by_project_roles(
    auth_client, admin, project_context
):
    assert auth_client(admin).get(METRICS).status_code == 200
    assert auth_client(project_context["owner"]).get(METRICS).status_code == 404
    assert auth_client(project_context["investor"]).get(STATUS).status_code == 404


@pytest.mark.django_db
def test_metrics_disabled_when_no_token_is_configured(settings):
    settings.METRICS_TOKEN = ""
    assert scrape("").status_code == 404
    assert scrape("test-metrics-token").status_code == 404


@pytest.mark.django_db
def test_scraper_token_does_not_open_the_status_endpoint():
    assert (
        APIClient().get(STATUS, HTTP_AUTHORIZATION="Bearer test-metrics-token").status_code == 404
    )


# --- Mesures ----------------------------------------------------------------------------------


@pytest.mark.django_db
def test_http_requests_are_counted_by_route_pattern_not_by_url(
    auth_client, project, project_context
):
    client = auth_client(project_context["owner"])
    client.get(f"/api/projects/{project.pk}/dashboard/")
    client.get(f"/api/projects/{project.pk}/dashboard/")
    client.get("/api/projects/999999/dashboard/")

    text = scrape().content.decode()

    route = "api/projects/<int:pk>/dashboard/"
    assert (
        sample(text, f'kemta_http_requests_total{{method="GET",route="{route}",status="200"}}') == 2
    )
    assert (
        sample(text, f'kemta_http_requests_total{{method="GET",route="{route}",status="404"}}') == 1
    )
    assert f"/api/projects/{project.pk}/" not in text  # jamais d'identifiant dans un libellé
    assert "kemta_http_request_duration_seconds_bucket" in text
    assert (
        sample(text, f'kemta_http_request_duration_seconds_count{{method="GET",route="{route}"}}')
        == 3
    )


@pytest.mark.django_db
def test_server_errors_are_counted(auth_client, project, project_context, monkeypatch):
    from apps.dashboard import views

    def boom(*args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(views, "build_project_dashboard", boom, raising=False)
    client = auth_client(project_context["owner"])
    client.raise_request_exception = False
    response = client.get(f"/api/projects/{project.pk}/dashboard/")
    assert response.status_code == 500

    text = scrape().content.decode()
    assert sample(text, 'kemta_http_errors_total{route="api/projects/<int:pk>/dashboard/"}') == 1


@pytest.mark.django_db
def test_sync_and_upload_helpers_expose_error_counters():
    metrics.record_sync("EVIDENCE_CAPTURE", "SYNCED")
    metrics.record_sync("EVIDENCE_CAPTURE", "CONFLICT", code="duplicate_evidence")
    metrics.record_sync("EVIDENCE_CAPTURE", "SYNCED", replayed=True)
    metrics.record_upload("evidence", "accepted", size=2048)
    metrics.record_upload("evidence", "rejected", reason="unsupported_media_type")

    text = scrape().content.decode()

    assert (
        sample(text, 'kemta_sync_errors_total{code="duplicate_evidence",type="EVIDENCE_CAPTURE"}')
        == 1
    )
    assert sample(text, 'kemta_sync_operations_total{status="synced",type="EVIDENCE_CAPTURE"}') == 2
    assert (
        sample(text, 'kemta_sync_operations_total{status="replayed",type="EVIDENCE_CAPTURE"}') == 1
    )
    assert sample(text, 'kemta_upload_bytes_total{kind="evidence"}') == 2048
    assert 'reason="unsupported_media_type"' in text


@pytest.mark.django_db
def test_upload_through_the_api_is_tracked(auth_client, project, project_context, photo):
    response = auth_client(project_context["agent"]).post(
        "/api/evidences/",
        {
            "project": project.pk,
            "file": photo(),
            "captured_at": timezone.now().isoformat(),
            "gps_status": "UNAVAILABLE",
        },
        format="multipart",
        headers={"Idempotency-Key": "ops-upload-1"},
    )
    assert response.status_code == 201, response.data

    text = scrape().content.decode()
    assert sample(text, 'kemta_uploads_total{kind="evidence",outcome="accepted"}') == 1
    assert sample(text, 'kemta_upload_bytes_total{kind="evidence"}') > 0


@pytest.mark.django_db
def test_rate_limited_responses_are_counted(monkeypatch):
    from rest_framework.throttling import SimpleRateThrottle

    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "login", "1/min")
    client = APIClient()
    for _ in range(3):
        client.post(
            "/api/auth/login/", {"phone_number": "+237690000000", "password": "x"}, format="json"
        )

    text = scrape().content.decode()
    assert sample(text, 'kemta_rate_limited_total{route="api/auth/login/"}') >= 1


@pytest.mark.django_db
def test_metrics_backend_outage_never_breaks_requests(monkeypatch, auth_client, project_context):
    def broken(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(metrics.get_backend(), "add", broken)
    assert auth_client(project_context["owner"]).get("/api/notifications/").status_code == 200
    metrics.incr("kemta_test_total")  # ne lève pas


@pytest.mark.django_db
def test_prometheus_output_is_well_formed():
    metrics.incr("kemta_http_requests_total", method="GET", route="x", status=200)
    metrics.observe("kemta_http_request_duration_seconds", 0.3, method="GET", route="x")

    text = metrics.render_prometheus()

    assert "# HELP kemta_http_requests_total" in text
    assert "# TYPE kemta_http_requests_total counter" in text
    assert "# TYPE kemta_http_request_duration_seconds histogram" in text
    assert text.endswith("\n")
    # Histogramme cumulatif : 0.3 s tombe dans les seaux ≥ 0.5 et +Inf.
    assert (
        sample(text, 'kemta_http_request_duration_seconds_bucket{le="+Inf",method="GET",route="x"}')
        == 1
    )
    assert (
        sample(text, 'kemta_http_request_duration_seconds_bucket{le="0.5",method="GET",route="x"}')
        == 1
    )


# --- Statut d'exploitation --------------------------------------------------------------------


@pytest.mark.django_db
def test_status_reports_components_queues_and_task_failures(auth_client, admin):
    TaskRun.objects.create(
        task_id="t-1",
        name="apps.notifications.tasks.dispatch_domain_event",
        state=TaskRun.State.FAILURE,
        error="RuntimeError: boom",
        retries=5,
        started_at=timezone.now(),
        finished_at=timezone.now(),
    )
    TaskRun.objects.create(
        task_id="t-2", name="x", state=TaskRun.State.SUCCESS, started_at=timezone.now()
    )

    response = auth_client(admin).get(STATUS)

    assert response.status_code == 200
    data = response.data
    assert data["components"]["database"]["ok"] is True
    assert data["celery"]["eager"] is True and "celery" in data["celery"]["queues"]
    assert data["tasks_24h"]["failure"] == 1 and data["tasks_24h"]["success"] == 1
    failure = data["tasks_24h"]["recent_failures"][0]
    assert failure["error"] == "RuntimeError: boom" and failure["retries"] == 5
    assert "version" in data and "metrics" in data


@pytest.mark.django_db
def test_status_contains_no_business_data(auth_client, admin, project, project_context):
    body = auth_client(admin).get(STATUS).content.decode()
    assert project.name not in body and "+237" not in body


@pytest.mark.django_db
def test_real_jwt_of_a_platform_admin_opens_the_ops_endpoints(admin, project_context):
    from apps.users.views import issue_tokens

    admin_token = issue_tokens(admin)["access"]
    owner_token = issue_tokens(project_context["owner"])["access"]

    def call(url, token):
        return APIClient().get(url, HTTP_AUTHORIZATION=f"Bearer {token}")

    assert call(METRICS, admin_token).status_code == 200
    assert call(STATUS, admin_token).status_code == 200
    assert call(METRICS, owner_token).status_code == 404
    assert call(STATUS, "n'importe-quoi").status_code == 404
