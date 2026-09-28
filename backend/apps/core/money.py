"""Montants en FCFA : entiers uniquement, jamais de centimes silencieuses."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from apps.core.exceptions import KemtaAPIError

MAX_AMOUNT = Decimal("999999999999999")


def to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def validate_fcfa_amount(value, *, field: str = "amount") -> Decimal:
    """Refuse les montants négatifs, non numériques, hors limites ou avec des centimes.

    Choix produit assumé (voir `docs/data-model.md`) : on **refuse** plutôt que d'arrondir,
    pour qu'aucun écart de trésorerie ne puisse apparaître sans décision explicite.
    """
    amount = to_decimal(value)
    if amount is None:
        raise KemtaAPIError("amount_invalid", "Montant invalide.", details={"field": field})
    if amount < 0:
        raise KemtaAPIError(
            "amount_invalid", "Le montant ne peut pas être négatif.", details={"field": field}
        )
    if amount > MAX_AMOUNT:
        raise KemtaAPIError("amount_invalid", "Montant hors limites.", details={"field": field})
    if amount != amount.to_integral_value():
        raise KemtaAPIError(
            "amount_has_cents",
            "Les montants sont exprimés en FCFA entiers : les centimes ne sont pas acceptés.",
            details={"field": field},
        )
    return amount


def as_int_amount(amount: Decimal | None) -> int | None:
    return None if amount is None else int(amount)
