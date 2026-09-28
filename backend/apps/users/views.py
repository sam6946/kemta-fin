"""Endpoints d'authentification — MVP-001, 002, 003 et **MVP-017 (mot de passe oublié)**."""

from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.activity import log_event
from apps.core.exceptions import KemtaAPIError
from apps.users.authentication import password_fingerprint
from apps.users.models import OTPCode, User
from apps.users.serializers import (
    EmailConfirmSerializer,
    EmailRequestSerializer,
    LoginSerializer,
    OTPResendSerializer,
    OTPVerifySerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)
from apps.users.services import sms
from apps.users.services.otp import issue_otp, verify_otp
from apps.users.services.password import (
    change_password,
    confirm_password_reset,
    mark_email_verified,
    request_password_reset,
)
from apps.users.services.phone import mask_phone
from apps.users.throttling import LoginThrottle, OTPRequestThrottle, PasswordResetThrottle


def issue_tokens(user: User) -> dict[str, str]:
    """Paire access/refresh.

    `role` et `phone` sont **indicatifs** (le backend reste l'autorité) ; `pwd_ts`
    est l'empreinte du mot de passe courant : elle permet d'invalider immédiatement
    les jetons émis avant un changement ou une réinitialisation de mot de passe.
    """
    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role
    refresh["phone"] = user.phone
    refresh["pwd_ts"] = password_fingerprint(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def auth_payload(user: User) -> dict:
    return {
        "access": None,
        "refresh": None,
        "user": UserSerializer(user).data,
        **issue_tokens(user),
    }


class RegisterView(APIView):
    """`POST /api/auth/register/` — inscription par numéro de téléphone."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [OTPRequestThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        issue_otp(purpose=OTPCode.Purpose.SIGNUP, phone=user.phone, user=user, request=request)
        log_event(
            "USER_REGISTERED",
            actor=user,
            entity_type="User",
            entity_id=user.pk,
            request=request,
        )
        return Response(
            {
                "phone_masked": mask_phone(user.phone),
                "otp_ttl": settings.OTP_TTL_SECONDS,
                "retry_in": settings.OTP_RESEND_COOLDOWN_SECONDS,
            },
            status=status.HTTP_201_CREATED,
        )


class OTPVerifyView(APIView):
    """`POST /api/auth/otp/verify/` — activation du compte (usage `SIGNUP`).

    Pour la réinitialisation du mot de passe, utiliser
    `POST /api/auth/password/reset/confirm/`.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [OTPRequestThrottle]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        otp = verify_otp(
            code=data["code"],
            purpose=data["purpose"],
            phone=data["phone"],
            request=request,
        )
        user = otp.user
        if user is None:
            raise KemtaAPIError("otp_invalid", "Code invalide ou expiré.")

        user.is_active = True
        user.is_phone_verified = True
        user.save(update_fields=["is_active", "is_phone_verified"])

        log_event(
            "USER_REGISTERED",
            actor=user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"outcome": "activated"},
            request=request,
        )
        return Response(auth_payload(user), status=status.HTTP_200_OK)


class OTPResendView(APIView):
    """`POST /api/auth/otp/resend/` — renvoi contrôlé (cooldown + quotas)."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [OTPRequestThrottle]

    def post(self, request):
        serializer = OTPResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.filter(phone=data["phone"], is_active=False).first()
        response_payload = {
            "detail": "Si ce numéro est en attente d'activation, un code vient d'être envoyé.",
            "retry_in": settings.OTP_RESEND_COOLDOWN_SECONDS,
            "otp_ttl": settings.OTP_TTL_SECONDS,
        }
        if user is not None:
            issue_otp(
                purpose=OTPCode.Purpose.SIGNUP,
                phone=user.phone,
                user=user,
                request=request,
            )
            log_event(
                "OTP_RESEND", actor=user, entity_type="User", entity_id=user.pk, request=request
            )
        return Response(response_payload, status=status.HTTP_200_OK)


class LoginView(APIView):
    """`POST /api/auth/login/` — téléphone + mot de passe."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [LoginThrottle, AnonRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.filter(phone=data["phone"]).first()
        # Message identique pour numéro inconnu et mot de passe erroné.
        invalid = KemtaAPIError("invalid_credentials", "Identifiants incorrects.", http_status=401)

        if user is None or not user.check_password(data["password"]):
            if user is not None:
                locked = user.register_login_failure()
                log_event(
                    "ACCOUNT_LOCKED" if locked else "LOGIN_FAILED",
                    actor=user,
                    entity_type="User",
                    entity_id=user.pk,
                    request=request,
                )
            else:
                log_event(
                    "LOGIN_FAILED",
                    entity_type="User",
                    metadata={"outcome": "unknown_phone"},
                    request=request,
                )
            raise invalid

        if not user.is_active:
            raise KemtaAPIError(
                "account_not_confirmed",
                "Compte non activé. Validez le code reçu par SMS.",
                http_status=403,
                details={"can_resend": True},
            )

        if user.is_locked:
            raise KemtaAPIError(
                "account_locked",
                "Compte temporairement verrouillé après plusieurs échecs.",
                http_status=429,
            )

        user.register_login_success()
        log_event(
            "LOGIN_SUCCESS", actor=user, entity_type="User", entity_id=user.pk, request=request
        )
        return Response(auth_payload(user), status=status.HTTP_200_OK)


class LogoutView(APIView):
    """`POST /api/auth/logout/` — révocation du refresh token."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            raise KemtaAPIError("missing_refresh_token", "Jeton de rafraîchissement manquant.")
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            raise KemtaAPIError("invalid_token", "Jeton invalide ou déjà révoqué.") from None
        log_event(
            "LOGOUT",
            actor=request.user,
            entity_type="User",
            entity_id=request.user.pk,
            request=request,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """`GET` / `PATCH /api/auth/me/` — profil courant (l'email passe par le flux OTP)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PasswordResetRequestView(APIView):
    """`POST /api/auth/password/reset/request/` — MVP-017, réponse **neutre**."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = request_password_reset(serializer.validated_data["phone"], request=request)
        return Response(payload, status=status.HTTP_200_OK)


class PasswordResetConfirmView(APIView):
    """`POST /api/auth/password/reset/confirm/` — OTP + nouveau mot de passe."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [PasswordResetThrottle]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        confirm_password_reset(
            phone=data["phone"],
            code=data["code"],
            new_password=data["new_password"],
            request=request,
        )
        return Response(
            {
                "detail": "Mot de passe réinitialisé. Connectez-vous avec votre nouveau mot de passe.",
                "sessions_revoked": True,
            },
            status=status.HTTP_200_OK,
        )


class PasswordChangeView(APIView):
    """`POST /api/auth/password/change/` — utilisateur connecté (ancien mot de passe requis)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = request.user
        change_password(
            user=user,
            new_password=serializer.validated_data["new_password"],
            request=request,
        )
        # Les autres appareils sont déconnectés ; la session courante est renouvelée.
        tokens = issue_tokens(user)
        return Response(
            {
                "detail": "Mot de passe modifié.",
                "sessions_revoked": True,
                **tokens,
            },
            status=status.HTTP_200_OK,
        )


class EmailRequestView(APIView):
    """`POST /api/auth/email/request/` — ajout facultatif de l'email (OTP)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()
        user = request.user
        # Le code part vers l'email à vérifier (aucun code par SMS dans ce flux).
        issue_otp(
            purpose=OTPCode.Purpose.EMAIL_VERIFY,
            email=email,
            user=user,
            request=request,
        )
        # Notification de sécurité, sans code, sur le numéro du compte.
        sms.send_sms(
            user.phone,
            "KEMTA : une adresse email est en cours d'ajout sur votre compte. "
            "Si vous n'êtes pas à l'origine de cette action, ignorez ce message.",
        )
        log_event(
            "EMAIL_ADDED",
            actor=user,
            entity_type="User",
            entity_id=user.pk,
            metadata={"outcome": "otp_sent"},
            request=request,
        )
        return Response(
            {"detail": "Un code de vérification a été envoyé à votre adresse email."},
            status=status.HTTP_200_OK,
        )


class EmailConfirmView(APIView):
    """`POST /api/auth/email/confirm/` — validation de l'email par OTP."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EmailConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        verify_otp(
            code=data["code"],
            purpose=OTPCode.Purpose.EMAIL_VERIFY,
            email=data["email"].lower(),
            user=request.user,
            request=request,
        )
        mark_email_verified(request.user, data["email"].lower(), request=request)
        return Response(UserSerializer(request.user).data, status=status.HTTP_200_OK)
