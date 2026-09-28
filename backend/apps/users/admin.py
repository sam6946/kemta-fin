from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import OTPCode, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Administration des utilisateurs : identifiant = téléphone (pas de `username`)."""

    ordering = ("-date_joined",)
    list_display = ("phone", "get_full_name", "role", "is_active", "is_phone_verified")
    list_filter = ("role", "is_active", "is_phone_verified")
    search_fields = ("phone", "first_name", "last_name", "email")
    readonly_fields = ("last_login", "date_joined", "password_changed_at", "locked_until")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        (
            _("Informations personnelles"),
            {"fields": ("first_name", "last_name", "email", "language")},
        ),
        (
            _("Rôles et statut"),
            {"fields": ("role", "is_active", "is_phone_verified", "email_verified_at")},
        ),
        (_("Sécurité"), {"fields": ("password_changed_at", "failed_login_count", "locked_until")}),
        (_("Permissions"), {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "first_name", "last_name", "password1", "password2", "role"),
            },
        ),
    )


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    """Lecture seule : le code en clair n'existe nulle part."""

    list_display = (
        "created_at",
        "phone",
        "email",
        "purpose",
        "expires_at",
        "consumed_at",
        "attempts",
    )
    list_filter = ("purpose", "channel")
    search_fields = ("phone", "email")

    def has_add_permission(self, request):
        return False
