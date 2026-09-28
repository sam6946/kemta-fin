from django.urls import path

from .views import (
    OrganizationDetailView,
    OrganizationListCreateView,
    OrganizationMemberDetailView,
    OrganizationMemberListCreateView,
)

urlpatterns = [
    path("organizations/", OrganizationListCreateView.as_view(), name="organization-list"),
    path("organizations/<int:pk>/", OrganizationDetailView.as_view(), name="organization-detail"),
    path(
        "organizations/<int:pk>/members/",
        OrganizationMemberListCreateView.as_view(),
        name="organization-members",
    ),
    path(
        "organizations/<int:pk>/members/<int:member_id>/",
        OrganizationMemberDetailView.as_view(),
        name="organization-member-detail",
    ),
]
