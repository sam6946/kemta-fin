"""Phase 8/11 — coût du dashboard : requêtes constantes, cache ciblé, invalidation.

Ces tests sont les garde-fous de performance du parcours principal : si quelqu'un ajoute une
requête *par ligne*, ils échouent (critère « aucune requête N+1 identifiée »).
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .conftest import URL, add_evidence

# Plafond documenté dans `docs/performance.md` : le dashboard d'un pilote (le plus complet).
MAX_QUERIES_MANAGER = 32


def count_queries(client, project):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(URL.format(project=project.pk))
    assert response.status_code == 200
    return len(queries), response


@pytest.mark.django_db
def test_query_count_is_bounded_and_independent_from_data_volume(
    auth_client, project, project_context, site, money, settings
):
    settings.DASHBOARD_CACHE_SECONDS = 0  # on mesure le calcul, pas le cache
    client = auth_client(project_context["owner"])
    small, _ = count_queries(client, project)

    from apps.evidences.models import EvidenceStatus
    from apps.projects.models import Milestone, Task

    for index in range(25):
        add_evidence(
            project,
            project_context["agent"],
            index=index,
            hours_old=index + 1,
            status=EvidenceStatus.VALIDATED if index % 2 else EvidenceStatus.PENDING,
        )
    for index in range(20):
        milestone = Milestone.objects.create(
            project=project,
            title=f"Jalon bis {index}",
            planned_date="2026-01-01",
            created_by=project_context["owner"],
        )
        Task.objects.create(
            project=project,
            milestone=milestone,
            title=f"Tâche bis {index}",
            planned_end_date="2026-01-10",
            created_by=project_context["owner"],
        )
    large, _ = count_queries(client, project)

    assert small <= MAX_QUERIES_MANAGER, f"{small} requêtes"
    assert large == small, (
        f"le volume de données ne doit pas changer le nombre de requêtes ({small} → {large})"
    )


@pytest.mark.django_db
def test_investor_and_agent_dashboards_are_cheaper_than_the_manager_one(
    auth_client, project, project_context, site, money, settings
):
    settings.DASHBOARD_CACHE_SECONDS = 0
    manager, _ = count_queries(auth_client(project_context["owner"]), project)
    agent, _ = count_queries(auth_client(project_context["agent"]), project)
    assert agent < manager


@pytest.mark.django_db
def test_second_call_is_served_from_cache_without_computation(
    auth_client, project, project_context, site, money
):
    client = auth_client(project_context["owner"])
    first_queries, first = count_queries(client, project)
    second_queries, second = count_queries(client, project)

    assert first["X-Cache"] == "MISS" and second["X-Cache"] == "HIT"
    assert second_queries < first_queries
    # Authentification (utilisateur) + périmètre projet seulement : plus aucun calcul agrégé.
    assert second_queries <= 4
    assert first.data == second.data


@pytest.mark.django_db
def test_a_write_invalidates_the_cache_immediately(
    auth_client, project, project_context, site, money
):
    client = auth_client(project_context["owner"])
    before = client.get(URL.format(project=project.pk))
    assert before.data["evidences"]["counts"]["pending"] == 0

    add_evidence(project, project_context["agent"], index=7)

    after = client.get(URL.format(project=project.pk))
    assert after["X-Cache"] == "MISS"
    assert after.data["evidences"]["counts"]["pending"] == 1


@pytest.mark.django_db
def test_financial_write_invalidates_the_cache(auth_client, project, project_context, site, money):
    from apps.finance import services

    client = auth_client(project_context["owner"])
    assert client.get(URL.format(project=project.pk)).data["budget"]["committed"] == 25_000_000

    services.transition_expense(
        expense=money["expenses"][2], actor=project_context["owner"], action="APPROVE"
    )

    assert client.get(URL.format(project=project.pk)).data["budget"]["committed"] == 26_500_000


@pytest.mark.django_db
def test_cache_is_per_user_so_permissions_never_leak(
    auth_client, project, project_context, site, money
):
    owner_view = auth_client(project_context["owner"]).get(URL.format(project=project.pk))
    agent_view = auth_client(project_context["agent"]).get(URL.format(project=project.pk))

    assert owner_view.data["budget"] is not None
    assert agent_view.data["budget"] is None
    assert agent_view["X-Cache"] == "MISS"


@pytest.mark.django_db
def test_cache_outage_never_breaks_the_dashboard(
    auth_client, project, project_context, site, monkeypatch
):
    from django.core.cache import cache

    def boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "get", boom)
    monkeypatch.setattr(cache, "set", boom)
    monkeypatch.setattr(cache, "incr", boom)

    response = auth_client(project_context["owner"]).get(URL.format(project=project.pk))

    assert response.status_code == 200
    assert response.data["progress"]["milestones_total"] == 3
