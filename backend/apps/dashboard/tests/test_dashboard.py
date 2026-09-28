"""MVP-011 — le dashboard agrégé : exactitude, alertes déterministes, permissions et cache."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.dashboard.services import build_project_dashboard
from apps.evidences.models import EvidenceStatus
from apps.finance.services import budget_summary
from apps.projects.progress import compute_project_progress

from .conftest import URL, add_evidence


def get(auth_client, user, project):
    return auth_client(user).get(URL.format(project=project.pk))


@pytest.mark.django_db
def test_owner_dashboard_aggregates_everything_from_the_backend(
    auth_client, project, project_context, site, money, today
):
    add_evidence(project, project_context["agent"], index=1)
    add_evidence(project, project_context["agent"], index=2, hours_old=80)
    add_evidence(project, project_context["agent"], index=3, status=EvidenceStatus.VALIDATED)

    response = get(auth_client, project_context["owner"], project)

    assert response.status_code == 200, response.data
    data = response.data
    assert data["audience"] == "manager"
    assert data["project"]["name"] == project.name
    # Avancement = celui du serveur (jamais recalculé côté client).
    assert data["progress"]["progress"] == float(compute_project_progress(project))
    assert data["progress"]["milestones_total"] == 3
    assert data["progress"]["milestones_done"] == 1
    assert data["progress"]["tasks_late"] == 1
    assert data["progress"]["milestones_late"] == 1
    # Jalons : dernier terminé et prochain à venir.
    assert data["milestones"]["last_completed"]["title"] == "Fondations"
    assert data["milestones"]["next"]["title"] == "Toiture"
    # Budget = synthèse financière du backend, à l'identique.
    summary = budget_summary(project)
    assert data["budget"]["planned"] == int(summary["planned"]) == 50_000_000
    assert data["budget"]["committed"] == int(summary["committed"]) == 25_000_000
    assert data["budget"]["balance"] == int(summary["balance"]) == 25_000_000
    assert data["budget"]["consumption_rate"] == float(summary["consumption_rate"]) == 50.0
    # Preuves et dépenses récentes, bornées.
    assert data["evidences"]["counts"] == {
        "pending": 2,
        "validated": 1,
        "rejected": 0,
        "flagged": 0,
        "stale": 1,
    }
    assert len(data["evidences"]["latest"]) == 3
    assert data["evidences"]["latest"][0]["thumbnail_url"].startswith("/api/evidences/")
    assert data["expenses"]["counts"]["submitted"] == 1
    assert len(data["expenses"]["latest"]) == 3
    assert data["activity"], "l'activité récente est visible du pilotage"
    assert data["permissions"]["view_finance"] is True


@pytest.mark.django_db
def test_alerts_are_deterministic_ordered_and_explained(
    auth_client, project, project_context, site, money, today
):
    add_evidence(project, project_context["agent"], index=1, hours_old=100)
    first = get(auth_client, project_context["owner"], project).data
    second = get(auth_client, project_context["owner"], project).data

    assert first["alerts"] == second["alerts"]
    codes = [alert["code"] for alert in first["alerts"]]
    assert "TASK_LATE" in codes and "MILESTONE_LATE" in codes
    assert "EVIDENCE_PENDING_STALE" in codes
    # Les alertes critiques passent avant les avertissements.
    severities = [alert["severity"] for alert in first["alerts"]]
    assert severities == sorted(severities, key={"critical": 0, "warning": 1, "info": 2}.get)
    task_alert = next(a for a in first["alerts"] if a["code"] == "TASK_LATE")
    assert task_alert["days_late"] == 20 and task_alert["severity"] == "critical"
    milestone_alert = next(a for a in first["alerts"] if a["code"] == "MILESTONE_LATE")
    assert milestone_alert["days_late"] == 9 and milestone_alert["severity"] == "critical"


@pytest.mark.django_db
def test_alerts_depend_only_on_data_and_reference_date(project, project_context, site, today):
    owner = project_context["owner"]
    project.refresh_from_db()
    now = build_project_dashboard(owner, project, today=today)
    later = build_project_dashboard(owner, project, today=today + timedelta(days=10))

    def late(payload, code):
        return next(a["days_late"] for a in payload["alerts"] if a["code"] == code)

    assert late(later, "TASK_LATE") == late(now, "TASK_LATE") + 10
    assert now["reference_date"] == today


@pytest.mark.django_db
def test_budget_alerts_at_80_percent_and_when_exceeded(
    auth_client, project, project_context, money
):
    from apps.finance import services

    owner, finance = project_context["owner"], project_context["finance"]
    services.transition_expense(expense=money["expenses"][2], actor=owner, action="APPROVE")
    # 26,5 M sur 50 M : 53 % → aucune alerte budgétaire globale.
    data = get(auth_client, owner, project).data
    assert data["budget"]["threshold"] == "OK"

    big = services.create_expense(
        project=project,
        actor=finance,
        data={"title": "Dalle", "amount": 15_000_000, "incurred_on": "2026-03-01"},
    )
    services.transition_expense(expense=big, actor=finance, action="SUBMIT")
    services.transition_expense(expense=big, actor=owner, action="APPROVE")
    data = get(auth_client, owner, project).data
    assert data["budget"]["threshold"] == "WARNING"
    assert any(a["code"] == "BUDGET_THRESHOLD_REACHED" for a in data["alerts"])


@pytest.mark.django_db
def test_project_outside_perimeter_is_a_404(auth_client, project, project_context):
    assert get(auth_client, project_context["stranger"], project).status_code == 404


@pytest.mark.django_db
def test_anonymous_is_refused(api, project):
    assert api.get(URL.format(project=project.pk)).status_code == 401


@pytest.mark.django_db
def test_field_agent_sees_no_finance_and_no_activity(
    auth_client, project, project_context, site, money
):
    data = get(auth_client, project_context["agent"], project).data

    assert data["audience"] == "field"
    assert data["budget"] is None and data["expenses"] is None
    assert data["activity"] is None
    assert data["permissions"]["view_finance"] is False
    assert not any(a["code"].startswith("BUDGET") for a in data["alerts"])
    assert data["progress"]["milestones_total"] == 3


@pytest.mark.django_db
def test_investor_dashboard_hides_unvalidated_work_and_unapproved_spending(
    auth_client, project, project_context, site, money
):
    agent = project_context["agent"]
    add_evidence(project, agent, index=1, status=EvidenceStatus.VALIDATED)
    add_evidence(project, agent, index=2, status=EvidenceStatus.PENDING)
    add_evidence(project, agent, index=3, status=EvidenceStatus.REJECTED)

    data = get(auth_client, project_context["investor"], project).data

    assert data["audience"] == "investor"
    assert data["budget"]["committed"] == 25_000_000
    assert data["evidences"]["counts"] == {"validated": 1, "stale": 0}
    assert [e["status"] for e in data["evidences"]["latest"]] == ["VALIDATED"]
    # Les brouillons et dépenses en attente ne sont pas montrés à l'investisseur.
    statuses = {e["status"] for e in data["expenses"]["latest"]}
    assert statuses <= {"APPROVED", "PAID"}
    assert set(data["expenses"]["counts"]) == {"approved", "paid"}
    assert not any(a["code"] == "EVIDENCE_PENDING_STALE" for a in data["alerts"])
    # Journal restreint aux décisions et à l'avancement : pas de saisie ni de membres.
    allowed = {
        "MILESTONE_CREATED",
        "MILESTONE_UPDATED",
        "EVIDENCE_VALIDATED",
        "EXPENSE_APPROVED",
        "PAYMENT_RECORDED",
        "BUDGET_THRESHOLD_REACHED",
        "BUDGET_EXCEEDED",
        "PROJECT_UPDATED",
    }
    assert {e["action"] for e in data["activity"]} <= allowed


@pytest.mark.django_db
def test_engineer_profile_and_permissions_match_backend(
    auth_client, project, project_context, site
):
    from apps.projects.access import resolve_capabilities

    engineer = project_context["engineer"]
    data = get(auth_client, engineer, project).data
    capabilities = resolve_capabilities(engineer, project)

    assert data["audience"] == "engineer"
    for key in ("manage_schedule", "capture_evidence", "validate_evidence", "view_finance"):
        assert data["permissions"][key] == capabilities[key], key


@pytest.mark.django_db
def test_empty_project_has_a_clear_empty_state(auth_client, project, project_context):
    data = get(auth_client, project_context["owner"], project).data

    assert data["progress"]["progress"] == 0
    assert data["progress"]["milestones_total"] == 0
    assert data["milestones"] == {"last_completed": None, "next": None}
    assert data["evidences"]["latest"] == []
    assert [a["code"] for a in data["alerts"]] == ["NO_PLANNING"]
    assert data["budget"]["committed"] == 0 and data["budget"]["balance"] == 50_000_000


@pytest.mark.django_db
def test_project_end_passed_alert(auth_client, project, project_context, site, today):
    type(project).objects.filter(pk=project.pk).update(planned_end_date=today - timedelta(days=3))

    data = get(auth_client, project_context["owner"], project).data

    alert = next(a for a in data["alerts"] if a["code"] == "PROJECT_END_PASSED")
    assert alert["days_late"] == 3 and alert["severity"] == "critical"


@pytest.mark.django_db
def test_collections_are_bounded(auth_client, project, project_context):
    agent = project_context["agent"]
    for index in range(12):
        add_evidence(project, agent, index=index, hours_old=index + 1)

    data = get(auth_client, project_context["owner"], project).data

    assert data["evidences"]["counts"]["pending"] == 12
    assert len(data["evidences"]["latest"]) == 5
    assert len(data["activity"]) <= 10
    assert data["alerts_total"] >= len(data["alerts"])


@pytest.mark.django_db
def test_dates_are_iso_and_reference_date_is_returned(auth_client, project, project_context, today):
    data = get(auth_client, project_context["owner"], project).data
    assert data["reference_date"] == today
    assert isinstance(data["reference_date"], date)
