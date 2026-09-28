"""Le seed de développement est utilisable… mais refusé en production."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.users.models import User
from apps.users.roles import ALL_ROLES


@pytest.mark.django_db
def test_seed_creates_one_account_per_role():
    call_command("seed_dev", stdout=StringIO())
    assert User.objects.count() == len(ALL_ROLES)
    assert set(User.objects.values_list("role", flat=True)) == set(ALL_ROLES)

    admin = User.objects.get(role="PLATFORM_ADMIN")
    assert admin.is_active and admin.is_phone_verified
    assert admin.check_password("Kemta#2026Demo")
    assert admin.phone.startswith("+237")


@pytest.mark.django_db
def test_seed_is_idempotent():
    call_command("seed_dev", stdout=StringIO())
    call_command("seed_dev", stdout=StringIO())
    assert User.objects.count() == len(ALL_ROLES)


@pytest.mark.django_db
def test_seed_is_refused_in_production(settings):
    settings.ENV = "production"
    with pytest.raises(CommandError):
        call_command("seed_dev", stdout=StringIO())
    assert User.objects.count() == 0
