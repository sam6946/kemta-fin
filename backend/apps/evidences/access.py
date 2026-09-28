"""Règles d'accès aux preuves terrain (périmètre projet)."""

from __future__ import annotations

from apps.evidences.models import Evidence
from apps.projects.access import accessible_projects


def accessible_evidences(user):
    """Preuves visibles : celles des projets accessibles (règle unique de périmètre)."""
    return Evidence.objects.filter(project__in=accessible_projects(user)).select_related(
        "project", "author", "task"
    )
