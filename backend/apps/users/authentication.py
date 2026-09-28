"""Authentification JWT : invalidation des jetons après changement de mot de passe."""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.settings import api_settings


def password_fingerprint(user) -> int:
    """Empreinte du mot de passe courant (millisecondes).

    Elle est embarquée dans le jeton : un jeton dont l'empreinte diffère de la
    valeur en base a été émis **avant** le dernier changement de mot de passe.
    """
    changed_at = getattr(user, "password_changed_at", None)
    if changed_at is None:
        return 0
    return int(changed_at.timestamp() * 1000)


class KemtaJWTAuthentication(JWTAuthentication):
    """JWT simplejwt + deux garde-fous :

    - un jeton émis **avant** le dernier changement de mot de passe est refusé
      (effet immédiat d'une réinitialisation, sans attendre l'expiration) ;
    - un utilisateur inactif ou supprimé logiquement est considéré comme inconnu.
    """

    def get_user(self, validated_token):
        user_model = self.user_model
        try:
            user = user_model.objects.get(
                **{api_settings.USER_ID_FIELD: validated_token[api_settings.USER_ID_CLAIM]}
            )
        except user_model.DoesNotExist:
            raise InvalidToken("Utilisateur introuvable.", code="user_not_found") from None

        if not user.is_active:
            raise InvalidToken("Compte inactif.", code="user_inactive")

        # Le jeton ne porte pas (encore) l'empreinte : on ne casse pas la compatibilité,
        # on vérifie uniquement quand l'information est disponible.
        token_fingerprint = validated_token.get("pwd_ts")
        if token_fingerprint is not None and token_fingerprint != password_fingerprint(user):
            raise InvalidToken(
                "Session invalidée par un changement de mot de passe.",
                code="password_changed",
            )
        return user
