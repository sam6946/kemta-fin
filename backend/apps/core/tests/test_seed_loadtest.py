"""`seed_loadtest` : volume de données de charge, refusé en production."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.evidences.models import Evidence
from apps.projects.models import Milestone, Task


@pytest.mark.django_db
def test_seed_loadtest_requires_demo_data():
    with pytest.raises(CommandError):
        call_command("seed_loadtest")


@pytest.mark.django_db
def test_seed_loadtest_adds_volume():
    call_command("seed_dev", verbosity=0)
    before = (Evidence.objects.count(), Task.objects.count(), Milestone.objects.count())
    call_command("seed_loadtest", evidences=30, milestones=4, tasks=12, expenses=6, verbosity=0)
    after = (Evidence.objects.count(), Task.objects.count(), Milestone.objects.count())
    assert after == (before[0] + 30, before[1] + 12, before[2] + 4)


@pytest.mark.django_db
def test_seed_loadtest_refused_in_production(settings):
    settings.IS_PRODUCTION = True
    with pytest.raises(CommandError, match="production"):
        call_command("seed_loadtest")


def test_loadtest_runner_percentile():
    path = Path(__file__).resolve().parents[3] / "loadtest" / "run.py"
    spec = importlib.util.spec_from_file_location("kemta_loadtest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.percentile([], 95) == 0.0
    assert module.percentile([10, 20, 30, 40, 100], 50) == 30
    assert module.percentile([10, 20, 30, 40, 100], 95) == 100
    assert ("dashboard", "/api/projects/7/dashboard/") in module.scenario(7)
