"""Outils de sérialisation partagés."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


class ModelValidationMixin:
    """Transforme les erreurs de `Model.clean()` en réponses 400 propres.

    Les modèles conservent leurs règles métier (défense en profondeur) ; sans ce
    mixin, une `ValidationError` Django remonterait en erreur 500 au lieu de 400.
    """

    def _save_with_model_validation(self, save_callable):
        try:
            return save_callable()
        except DjangoValidationError as exc:
            detail = getattr(exc, "message_dict", None) or {"detail": exc.messages}
            raise serializers.ValidationError(detail) from exc

    def create(self, validated_data):
        instance = self.Meta.model(**validated_data)
        self._save_with_model_validation(instance.save)
        return instance

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        self._save_with_model_validation(instance.save)
        return instance


class FcfaField(serializers.DecimalField):
    """Montant en FCFA.

    En entrée, on accepte une écriture décimale afin de pouvoir **refuser explicitement**
    les centimes (`amount_has_cents`) au lieu d'un message générique. En sortie, la valeur
    est toujours un entier : l'API ne manipule jamais de centimes.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", 2)
        super().__init__(**kwargs)

    def to_representation(self, value):
        return None if value is None else int(value)


def iso(value):
    return value.isoformat() if value is not None else None
