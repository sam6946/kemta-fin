from django.urls import path

from .planning_views import (
    MilestoneDetailView,
    MilestoneListCreateView,
    ProjectDelaysView,
    ProjectScheduleView,
    TaskDetailView,
    TaskListCreateView,
)
from .views import (
    ProjectDetailView,
    ProjectListCreateView,
    ProjectMemberDetailView,
    ProjectMemberListCreateView,
)

urlpatterns = [
    path("projects/", ProjectListCreateView.as_view(), name="project-list"),
    path("projects/<int:pk>/", ProjectDetailView.as_view(), name="project-detail"),
    path(
        "projects/<int:pk>/members/",
        ProjectMemberListCreateView.as_view(),
        name="project-members",
    ),
    path(
        "projects/<int:pk>/members/<int:member_id>/",
        ProjectMemberDetailView.as_view(),
        name="project-member-detail",
    ),
    # Phase 4 — jalons, tâches, planning et retards.
    path(
        "projects/<int:pk>/milestones/",
        MilestoneListCreateView.as_view(),
        name="project-milestones",
    ),
    path(
        "projects/<int:pk>/tasks/",
        TaskListCreateView.as_view(),
        name="project-tasks",
    ),
    path(
        "projects/<int:pk>/schedule/",
        ProjectScheduleView.as_view(),
        name="project-schedule",
    ),
    path(
        "projects/<int:pk>/delays/",
        ProjectDelaysView.as_view(),
        name="project-delays",
    ),
    path("milestones/<int:pk>/", MilestoneDetailView.as_view(), name="milestone-detail"),
    path("tasks/<int:pk>/", TaskDetailView.as_view(), name="task-detail"),
]
