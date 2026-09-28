from django.urls import path

from .views import (
    EvidenceCreateView,
    EvidenceDetailView,
    EvidenceFileView,
    EvidenceHistoryView,
    EvidencePendingCountView,
    EvidenceTransitionView,
    ProjectEvidenceListView,
)

urlpatterns = [
    path("evidences/", EvidenceCreateView.as_view(), name="evidence-create"),
    path("evidences/pending/", EvidencePendingCountView.as_view(), name="evidence-pending"),
    path("evidences/<int:pk>/", EvidenceDetailView.as_view(), name="evidence-detail"),
    path(
        "evidences/<int:pk>/history/",
        EvidenceHistoryView.as_view(),
        name="evidence-history",
    ),
    path(
        "evidences/<int:pk>/transition/",
        EvidenceTransitionView.as_view(),
        name="evidence-transition",
    ),
    path(
        "evidences/<int:pk>/file/",
        EvidenceFileView.as_view(),
        {"variant": "file"},
        name="evidence-file",
    ),
    path(
        "evidences/<int:pk>/thumbnail/",
        EvidenceFileView.as_view(),
        {"variant": "thumbnail"},
        name="evidence-thumbnail",
    ),
    path(
        "projects/<int:pk>/evidences/",
        ProjectEvidenceListView.as_view(),
        name="project-evidences",
    ),
]
