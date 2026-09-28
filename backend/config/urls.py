from django.contrib import admin
from django.urls import include, path

from apps.core.activity_views import (
    ActivityMetaView,
    GlobalActivityView,
    MyActivityView,
    ProjectActivityView,
)
from apps.core.dev_views import DevOutboxView
from apps.core.ops_views import MetricsView, StatusView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", include("apps.core.urls")),
    # Outils de développement : renvoient 404 hors environnement de développement.
    path("api/dev/outbox/", DevOutboxView.as_view(), name="dev-outbox"),
    # Phase 9 — journal d'activité (lecture seule) ; phase 10/11 — exploitation.
    path("api/projects/<int:pk>/activity/", ProjectActivityView.as_view(), name="project-activity"),
    path("api/activity/", GlobalActivityView.as_view(), name="activity-global"),
    path("api/activity/mine/", MyActivityView.as_view(), name="activity-mine"),
    path("api/activity/meta/", ActivityMetaView.as_view(), name="activity-meta"),
    path("api/ops/metrics/", MetricsView.as_view(), name="ops-metrics"),
    path("api/ops/status/", StatusView.as_view(), name="ops-status"),
    path("api/auth/", include("apps.users.urls")),
    path("api/meta/", include("apps.users.urls_meta")),
    path("api/", include("apps.organizations.urls")),
    path("api/", include("apps.projects.urls")),
    path("api/", include("apps.evidences.urls")),
    path("api/", include("apps.sync.urls")),
    path("api/", include("apps.finance.urls")),
    path("api/", include("apps.dashboard.urls")),
    path("api/", include("apps.notifications.urls")),
]
