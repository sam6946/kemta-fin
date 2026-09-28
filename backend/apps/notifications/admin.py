from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "event_type", "title", "count", "read_at", "last_event_at")
    list_filter = ("event_type",)
    search_fields = ("title", "user__phone")
    readonly_fields = [field.name for field in Notification._meta.fields]

    def has_add_permission(self, request):
        return False
