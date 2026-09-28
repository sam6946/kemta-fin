from django.contrib import admin

from .models import Project, ProjectMember


class ProjectMemberInline(admin.TabularInline):
    model = ProjectMember
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "organization",
        "status",
        "budget_total",
        "currency",
        "progress",
    )
    list_filter = ("status", "currency", "organization")
    search_fields = ("name", "code", "city", "location_label")
    autocomplete_fields = ("organization", "created_by")
    inlines = [ProjectMemberInline]


@admin.register(ProjectMember)
class ProjectMemberAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "user",
        "role",
        "can_validate_evidence",
        "can_manage_finance",
        "is_active",
    )
    list_filter = ("role", "is_active", "can_validate_evidence", "can_manage_finance")
    search_fields = ("project__name", "user__phone")
