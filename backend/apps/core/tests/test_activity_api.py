"""MVP-012 (phase 9) — consultation du journal : rôles, filtres, lecture seule, protection."""

from __future__ import annotations

import pytest
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.activity import log_event
from apps.core.activity_views import ACTION_GROUPS, group_of
from apps.core.models import ActivityLog
from apps.users.roles import Role

PROJECT_URL = "/api/projects/{id}/activity/"
GLOBAL_URL = "/api/activity/"


@pytest.fixture()
def admin(make_user):
    return make_user(Role.PLATFORM_ADMIN, phone_number="+237699000001")


@pytest.fixture()
def events(project, project_context):
    """Trois événements de projet + un événement d'authentification + un autre projet."""
    owner = project_context["owner"]
    org = project.organization
    log_event(
        "MILESTONE_CREATED",
        actor=owner,
        entity_type="Milestone",
        entity_id=1,
        organization=org,
        project=project,
        metadata={"title": "Fondations"},
    )
    log_event(
        "EXPENSE_APPROVED",
        actor=project_context["finance"],
        entity_type="Expense",
        entity_id=7,
        organization=org,
        project=project,
    )
    log_event(
        "EVIDENCE_REJECTED",
        actor=project_context["validator"],
        entity_type="Evidence",
        entity_id=3,
        organization=org,
        project=project,
    )
    log_event("LOGIN_SUCCESS", actor=owner, entity_type="User", entity_id=owner.pk)


def fetch(auth_client, user, url, **params):
    return auth_client(user).get(url, params)


@pytest.mark.django_db
def test_every_action_belongs_to_exactly_one_group():
    """Un nouvel événement ajouté sans famille échoue ici : il resterait invisible du journal."""
    grouped = [action for actions in ACTION_GROUPS.values() for action in actions]
    assert len(grouped) == len(set(grouped)), "action présente dans deux familles"
    missing = set(ActivityLog.Action.values) - set(grouped)
    assert not missing, f"actions sans famille : {sorted(missing)}"
    unknown = set(grouped) - set(ActivityLog.Action.values)
    assert not unknown, f"familles avec actions inconnues : {sorted(unknown)}"
    assert group_of("LOGIN_SUCCESS") == "auth" and group_of("TASK_FAILED") == "system"


@pytest.mark.django_db
def test_project_activity_has_actor_action_entity_project_and_date(
    auth_client, project, project_context, events
):
    response = fetch(auth_client, project_context["owner"], PROJECT_URL.format(id=project.pk))

    assert response.status_code == 200, response.data
    assert response.data["count"] == 3  # l'événement d'authentification n'y figure pas
    first = response.data["results"][0]
    assert first["action"] == "EVIDENCE_REJECTED" and first["action_label"]
    assert first["entity_type"] == "Evidence" and first["entity_id"] == "3"
    assert first["project"] == project.pk and first["created_at"]
    assert first["actor"]["id"] == project_context["validator"].pk
    assert first["group"] == "evidences"
    # Le plus récent d'abord.
    dates = [item["created_at"] for item in response.data["results"]]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.django_db
def test_network_details_are_hidden_from_project_roles(
    auth_client, project, project_context, events, admin
):
    ActivityLog.objects.create(
        action="PROJECT_UPDATED",
        project=project,
        organization=project.organization,
        entity_type="Project",
        ip_address="10.0.0.9",
        user_agent="UA",
    )
    owner_view = fetch(auth_client, project_context["owner"], PROJECT_URL.format(id=project.pk))
    admin_view = fetch(auth_client, admin, PROJECT_URL.format(id=project.pk))

    assert "ip_address" not in owner_view.data["results"][0]
    assert "user_agent" not in owner_view.data["results"][0]
    assert admin_view.data["results"][0]["ip_address"] == "10.0.0.9"


@pytest.mark.django_db
def test_role_restrictions(auth_client, project, project_context, events):
    url = PROJECT_URL.format(id=project.pk)

    assert fetch(auth_client, project_context["stranger"], url).status_code == 404
    assert fetch(auth_client, project_context["agent"], url).status_code == 403
    for role in ("owner", "engineer", "finance", "validator", "investor"):
        assert fetch(auth_client, project_context[role], url).status_code == 200, role
    from rest_framework.test import APIClient

    assert APIClient().get(url).status_code == 401


@pytest.mark.django_db
def test_filters_by_group_action_entity_actor_and_date(
    auth_client, project, project_context, events
):
    url = PROJECT_URL.format(id=project.pk)
    owner = project_context["owner"]

    assert fetch(auth_client, owner, url, group="finance").data["count"] == 1
    assert fetch(auth_client, owner, url, action="milestone_created").data["count"] == 1
    assert (
        fetch(auth_client, owner, url, action="EXPENSE_APPROVED,EVIDENCE_REJECTED").data["count"]
        == 2
    )
    assert fetch(auth_client, owner, url, entity_type="Expense", entity_id="7").data["count"] == 1
    assert fetch(auth_client, owner, url, actor=project_context["finance"].pk).data["count"] == 1
    today = timezone.localdate().isoformat()
    assert fetch(auth_client, owner, url, since=today, until=today).data["count"] == 3
    assert fetch(auth_client, owner, url, since="2999-01-01").data["count"] == 0


@pytest.mark.django_db
def test_invalid_filters_are_clear_400_errors(auth_client, project, project_context, events):
    url = PROJECT_URL.format(id=project.pk)
    owner = project_context["owner"]

    for params in ({"group": "nope"}, {"action": "HACK"}, {"since": "hier"}, {"group": "auth"}):
        response = fetch(auth_client, owner, url, **params)
        assert response.status_code == 400, params
        assert response.data["error"]["code"] == "invalid_parameter"


@pytest.mark.django_db
def test_pagination_is_bounded(auth_client, project, project_context):
    for index in range(130):
        log_event(
            "TASK_UPDATED",
            entity_type="Task",
            entity_id=index,
            organization=project.organization,
            project=project,
        )
    client = auth_client(project_context["owner"])
    url = PROJECT_URL.format(id=project.pk)

    page = client.get(url).data
    assert len(page["results"]) == 20 and page["count"] == 130 and page["next"]
    assert len(client.get(url, {"page_size": 1000}).data["results"]) == 100

    with CaptureQueriesContext(connection) as queries:
        client.get(url, {"page_size": 100})
    assert len(queries) <= 12  # aucune requête par ligne (acteurs préchargés)


@pytest.mark.django_db
def test_journal_is_read_only_through_the_api(auth_client, project, project_context, events):
    client = auth_client(project_context["owner"])
    for url in (PROJECT_URL.format(id=project.pk), GLOBAL_URL):
        for verb in ("post", "put", "patch", "delete"):
            assert getattr(client, verb)(url, {}, format="json").status_code == 405, (verb, url)
    entry = ActivityLog.objects.first()
    assert client.delete(f"{GLOBAL_URL}{entry.pk}/").status_code == 404


@pytest.mark.django_db
def test_global_journal_is_for_platform_admins_only(
    auth_client, project, project_context, events, admin
):
    assert fetch(auth_client, project_context["owner"], GLOBAL_URL).status_code == 403
    response = fetch(auth_client, admin, GLOBAL_URL)
    assert response.status_code == 200 and response.data["count"] >= 4
    assert fetch(auth_client, admin, GLOBAL_URL, group="auth").data["count"] >= 1
    assert fetch(auth_client, admin, GLOBAL_URL, project=project.pk).data["count"] == 3


@pytest.mark.django_db
def test_my_activity_shows_only_my_security_events(auth_client, project_context, events):
    log_event("LOGIN_SUCCESS", actor=project_context["engineer"], entity_type="User")

    response = fetch(auth_client, project_context["owner"], GLOBAL_URL + "mine/")

    assert response.status_code == 200
    assert {item["action"] for item in response.data["results"]} == {"LOGIN_SUCCESS"}
    assert {item["actor"]["id"] for item in response.data["results"]} == {
        project_context["owner"].pk
    }


@pytest.mark.django_db
def test_meta_lists_groups_according_to_role(auth_client, project_context, admin):
    owner_groups = {
        g["code"]
        for g in fetch(auth_client, project_context["owner"], GLOBAL_URL + "meta/").data["groups"]
    }
    admin_groups = {
        g["code"] for g in fetch(auth_client, admin, GLOBAL_URL + "meta/").data["groups"]
    }

    assert "auth" not in owner_groups and "finance" in owner_groups
    assert "auth" in admin_groups


# --- Protection : les événements critiques ne sont jamais supprimés -----------------------------


@pytest.mark.django_db
def test_critical_events_survive_soft_deletion_and_cannot_be_removed(project, project_context):
    entry = log_event(
        "PROJECT_UPDATED", entity_type="Project", entity_id=project.pk, project=project
    )

    project.delete()  # suppression logique du projet

    assert ActivityLog.objects.filter(pk=entry.pk).exists()
    with pytest.raises(IntegrityError):
        ActivityLog.objects.filter(pk=entry.pk).delete()
    with pytest.raises(IntegrityError):
        ActivityLog.objects.filter(pk=entry.pk).update(action="LOGOUT")
    with pytest.raises(IntegrityError):
        entry.delete()


@pytest.mark.django_db
def test_soft_deleted_items_disappear_from_normal_views_but_history_remains(
    auth_client, project, project_context, organization
):
    from apps.projects.models import Milestone

    owner = project_context["owner"]
    milestone = Milestone.objects.create(
        project=project, title="À retirer", planned_date=timezone.localdate(), created_by=owner
    )
    client = auth_client(owner)
    assert client.delete(f"/api/milestones/{milestone.pk}/").status_code in (200, 204)

    listing = client.get(f"/api/projects/{project.pk}/milestones/").data["results"]
    assert milestone.pk not in [item["id"] for item in listing]
    assert Milestone.all_objects.filter(pk=milestone.pk, deleted_at__isnull=False).exists()
    assert ActivityLog.objects.filter(action="MILESTONE_DELETED", entity_id=milestone.pk).exists()
