"""API des notifications in-app : isolation, lecture, pagination, périmètre projet."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.notifications.models import Notification
from apps.projects.models import ProjectMember

URL = "/api/notifications/"


def make(user, project, key, **extra):
    return Notification.objects.create(
        user=user,
        project=project,
        event_type=extra.pop("event_type", "MilestoneValidated"),
        group_key=key,
        title=extra.pop("title", f"Notification {key}"),
        **extra,
    )


@pytest.mark.django_db
def test_anonymous_is_refused(api):
    assert api.get(URL).status_code == 401
    assert api.post(URL + "read-all/").status_code == 401


@pytest.mark.django_db
def test_user_only_sees_their_own_notifications(auth_client, project, project_context):
    mine = make(project_context["owner"], project, "a")
    make(project_context["engineer"], project, "b")

    response = auth_client(project_context["owner"]).get(URL)

    assert response.status_code == 200
    assert [n["id"] for n in response.data["results"]] == [mine.pk]
    assert response.data["unread_count"] == 1 and response.data["count"] == 1


@pytest.mark.django_db
def test_mark_as_read_is_idempotent_and_reduces_the_counter(auth_client, project, project_context):
    note = make(project_context["owner"], project, "a")
    make(project_context["owner"], project, "b")
    client = auth_client(project_context["owner"])

    first = client.post(f"{URL}{note.pk}/read/")
    stamp = Notification.objects.get(pk=note.pk).read_at
    second = client.post(f"{URL}{note.pk}/read/")

    assert first.status_code == second.status_code == 200
    assert first.data["is_read"] is True
    assert Notification.objects.get(pk=note.pk).read_at == stamp
    assert client.get(URL + "unread-count/").data == {"unread_count": 1}


@pytest.mark.django_db
def test_cannot_read_someone_elses_notification(auth_client, project, project_context):
    note = make(project_context["engineer"], project, "a")

    response = auth_client(project_context["owner"]).post(f"{URL}{note.pk}/read/")

    assert response.status_code == 404
    assert Notification.objects.get(pk=note.pk).read_at is None


@pytest.mark.django_db
def test_read_all_only_touches_the_caller(auth_client, project, project_context):
    make(project_context["owner"], project, "a")
    make(project_context["owner"], project, "b")
    other = make(project_context["engineer"], project, "c")

    response = auth_client(project_context["owner"]).post(URL + "read-all/")

    assert response.data == {"marked": 2, "unread_count": 0}
    assert Notification.objects.get(pk=other.pk).read_at is None


@pytest.mark.django_db
def test_unread_filter_and_pagination(auth_client, project, project_context):
    owner = project_context["owner"]
    for index in range(25):
        make(owner, project, f"g{index}")
    Notification.objects.filter(group_key__in=["g0", "g1"]).update(read_at=timezone.now())
    client = auth_client(owner)

    page = client.get(URL)
    assert len(page.data["results"]) == 20 and page.data["count"] == 25 and page.data["next"]
    unread = client.get(URL, {"unread": "1", "page_size": 100})
    assert unread.data["count"] == 23
    assert all(not item["is_read"] for item in unread.data["results"])


@pytest.mark.django_db
def test_notifications_of_a_project_the_user_left_are_hidden(auth_client, project, project_context):
    engineer = project_context["engineer"]
    note = make(engineer, project, "a")
    ProjectMember.objects.filter(project=project, user=engineer).update(is_active=False)

    client = auth_client(engineer)

    assert client.get(URL).data["results"] == []
    assert client.get(URL + "unread-count/").data["unread_count"] == 0
    assert client.post(f"{URL}{note.pk}/read/").status_code == 404


@pytest.mark.django_db
def test_list_query_count_is_constant(auth_client, project, project_context):
    owner = project_context["owner"]
    client = auth_client(owner)

    def measure():
        with CaptureQueriesContext(connection) as queries:
            assert client.get(URL, {"page_size": 100}).status_code == 200
        return len(queries)

    make(owner, project, "first")
    small = measure()
    for index in range(40):
        make(owner, project, f"more-{index}")
    assert measure() == small
