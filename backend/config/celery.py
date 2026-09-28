import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("kemta")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# Les tâches planifiées vivent dans `settings.CELERY_BEAT_SCHEDULE` (source unique).
