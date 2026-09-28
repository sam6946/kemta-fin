from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"
    verbose_name = "Cœur (journalisation, santé, pages communes)"

    def ready(self) -> None:
        # Enregistre les signaux Celery (suivi des tâches, phase 10).
        from . import task_tracking  # noqa: F401
