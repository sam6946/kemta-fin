from django.urls import path

from .views import ProjectDashboardView, WorkspaceView

urlpatterns = [
    path("projects/<int:pk>/dashboard/", ProjectDashboardView.as_view(), name="project-dashboard"),
    path("workspace/", WorkspaceView.as_view(), name="workspace"),
]
