"""MVP-012 — le journal d'activité est immuable et nettoie les données sensibles."""

import pytest
from django.db import IntegrityError as DjangoIntegrityError

from apps.core.activity import log_event
from apps.core.models import ActivityLog


@pytest.mark.django_db
def test_log_event_creates_entry():
    entry = log_event(
        "USER_REGISTERED", entity_type="User", entity_id=42, metadata={"phone": "+237690000000"}
    )
    assert entry is not None
    assert entry.action == "USER_REGISTERED"
    assert entry.entity_id == "42"
    assert ActivityLog.objects.count() == 1


@pytest.mark.django_db
def test_log_event_strips_secrets():
    entry = log_event(
        "PASSWORD_RESET_CONFIRMED",
        entity_type="User",
        metadata={
            "password": "secret",
            "new_password": "secret2",
            "code": "123456",
            "sessions_revoked": 3,
        },
    )
    assert entry.metadata == {"sessions_revoked": 3}


@pytest.mark.django_db
def test_entries_cannot_be_modified():
    entry = log_event("USER_REGISTERED", entity_type="User")
    entry.action = "LOGIN_SUCCESS"
    with pytest.raises(DjangoIntegrityError):
        entry.save()


@pytest.mark.django_db
def test_entries_cannot_be_deleted():
    log_event("USER_REGISTERED", entity_type="User")
    with pytest.raises(DjangoIntegrityError):
        ActivityLog.objects.all().delete()
    with pytest.raises(DjangoIntegrityError):
        ActivityLog.objects.first().delete()
    assert ActivityLog.objects.count() == 1


@pytest.mark.django_db
def test_queryset_update_is_forbidden():
    log_event("USER_REGISTERED", entity_type="User")
    with pytest.raises(DjangoIntegrityError):
        ActivityLog.objects.all().update(action="LOGOUT")
