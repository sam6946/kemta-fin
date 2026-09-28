"""Routes de la synchronisation hors ligne (MVP-009)."""

from django.urls import path

from apps.sync.views import SyncBatchView, SyncOperationReplayView, SyncStatusView

urlpatterns = [
    path("sync/batch/", SyncBatchView.as_view(), name="sync-batch"),
    path("sync/status/", SyncStatusView.as_view(), name="sync-status"),
    path(
        "sync/operations/<str:idempotency_key>/forget/",
        SyncOperationReplayView.as_view(),
        name="sync-operation-forget",
    ),
]
