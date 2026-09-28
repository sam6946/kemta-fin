from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.dashboard"
    verbose_name = "Tableaux de bord agrégés"

    def ready(self) -> None:
        from . import signals  # noqa: F401
