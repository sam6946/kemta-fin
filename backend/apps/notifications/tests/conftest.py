from __future__ import annotations

import pytest

from apps.evidences.tests.conftest import photo  # noqa: F401  (fixture réutilisée)
from apps.finance.tests.conftest import (  # noqa: F401
    budget_line,
    expense,
    finance_context,
    funded_project,
    transition,
)
from apps.notifications.models import Notification


@pytest.fixture()
def notes():
    """`notes(user)` → notifications du destinataire (ordre chronologique)."""

    def _notes(user, **filters):
        return list(Notification.objects.filter(user=user, **filters).order_by("id"))

    return _notes
