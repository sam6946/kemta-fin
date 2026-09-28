"""Les outils de développement ne doivent jamais fuiter en production."""

import pytest


@pytest.mark.django_db
def test_outbox_is_available_in_development(api, settings):
    settings.ENABLE_DEV_OUTBOX = True
    response = api.get("/api/dev/outbox/")
    assert response.status_code == 200
    assert response.data["sms"] == []
    assert "production" in response.data["notice"]


@pytest.mark.django_db
def test_outbox_returns_sent_messages(api, settings, register_and_activate):
    settings.ENABLE_DEV_OUTBOX = True
    register_and_activate()
    response = api.get("/api/dev/outbox/")
    assert response.status_code == 200
    assert len(response.data["sms"]) == 1
    assert "code de vérification" in response.data["sms"][0]["message"]


@pytest.mark.django_db
def test_outbox_is_hidden_when_disabled(api, settings):
    settings.ENABLE_DEV_OUTBOX = False
    response = api.get("/api/dev/outbox/")
    assert response.status_code == 404


def test_dev_tools_guard_follows_the_setting(settings):
    """Le garde-fou lit strictement le réglage : en production il vaut False."""
    from apps.core.dev_views import dev_tools_enabled

    settings.ENABLE_DEV_OUTBOX = False
    assert dev_tools_enabled() is False
    settings.ENABLE_DEV_OUTBOX = True
    assert dev_tools_enabled() is True


def test_default_value_is_disabled_outside_debug():
    """La valeur par défaut est `DEBUG and not production` (voir config/settings.py)."""
    import importlib

    source = (
        __import__("pathlib").Path(__file__).resolve().parents[3] / "config" / "settings.py"
    ).read_text()
    assert 'env.bool("ENABLE_DEV_OUTBOX", default=DEBUG and not IS_PRODUCTION)' in source
    # Et le module reste importable sans erreur.
    importlib.import_module("config.settings_test")
