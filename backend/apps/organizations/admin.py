from django.contrib import admin

from .models import Organization, OrganizationMember


class OrganizationMemberInline(admin.TabularInline):
    model = OrganizationMember
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "city", "owner", "is_active", "created_at")
    list_filter = ("type", "is_active", "country")
    search_fields = ("name", "slug", "city")
    autocomplete_fields = ("owner",)
    inlines = [OrganizationMemberInline]
    readonly_fields = ("slug",)


@admin.register(OrganizationMember)
class OrganizationMemberAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("organization__name", "user__phone", "user__first_name", "user__last_name")
