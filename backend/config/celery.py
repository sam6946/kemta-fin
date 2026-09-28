import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("kemta")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Tâches planifiées (celery beat, sans dépendance à la base de données).
app.conf.beat_schedule = {
    "purge-expired-otps": {
        "task": "apps.users.tasks.purge_expired_otps",
        "schedule": 21600.0,  # toutes les 6 heures
    },
}
