"""Entrée du lot de synchronisation (MVP-009)."""

from __future__ import annotations

from rest_framework import serializers

from apps.sync.operations import SUPPORTED_OPERATIONS

MAX_OPERATIONS_PER_BATCH = 50


class SyncOperationInputSerializer(serializers.Serializer):
    """Une opération telle que l'appareil l'a mise en file."""

    op_id = serializers.CharField(max_length=64)
    type = serializers.ChoiceField(choices=SUPPORTED_OPERATIONS)
    idempotency_key = serializers.CharField(max_length=64)
    payload = serializers.DictField(required=False, default=dict)


class SyncBatchInputSerializer(serializers.Serializer):
    operations = SyncOperationInputSerializer(many=True, allow_empty=False)

    def validate_operations(self, value):
        if len(value) > MAX_OPERATIONS_PER_BATCH:
            raise serializers.ValidationError(
                f"Un lot ne peut pas dépasser {MAX_OPERATIONS_PER_BATCH} opérations."
            )
        # Une clé ne peut pas apparaître deux fois dans le même lot : sinon la seconde
        # opération serait rejouée avec la réponse de la première.
        keys = [operation["idempotency_key"] for operation in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError(
                "Deux opérations du lot partagent la même clé d'idempotence."
            )
        op_ids = [operation["op_id"] for operation in value]
        if len(op_ids) != len(set(op_ids)):
            raise serializers.ValidationError("Deux opérations du lot partagent le même op_id.")
        return value
