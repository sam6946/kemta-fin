"""Administration : consultation seule pour l'historique (append-only côté ORM)."""

from django.contrib import admin

from apps.evidences.models import Evidence, EvidenceValidation


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "author",
        "status",
        "gps_status",
        "captured_at",
        "sync_status",
    )
    list_filter = ("status", "sync_status", "gps_status")
    search_fields = ("description", "hash_sha256", "project__name", "author__phone")
    readonly_fields = ("hash_sha256", "received_at", "size_bytes", "content_type")
    date_hierarchy = "captured_at"


@admin.register(EvidenceValidation)
class EvidenceValidationAdmin(admin.ModelAdmin):
    """Journal des validations : lecture seule (immuable par conception)."""

    list_display = ("id", "evidence", "actor", "action", "from_status", "to_status", "created_at")
    list_filter = ("action", "to_status")
    search_fields = ("comment",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
