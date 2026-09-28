from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from .views import (
    EmailConfirmView,
    EmailRequestView,
    LoginView,
    LogoutView,
    MeView,
    OTPResendView,
    OTPVerifyView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("otp/verify/", OTPVerifyView.as_view(), name="auth-otp-verify"),
    path("otp/resend/", OTPResendView.as_view(), name="auth-otp-resend"),
    path("login/", LoginView.as_view(), name="auth-login"),
    path("token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="auth-token-verify"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="auth-me"),
    path(
        "password/reset/request/",
        PasswordResetRequestView.as_view(),
        name="auth-password-reset-request",
    ),
    path(
        "password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
    path("password/change/", PasswordChangeView.as_view(), name="auth-password-change"),
    path("email/request/", EmailRequestView.as_view(), name="auth-email-request"),
    path("email/confirm/", EmailConfirmView.as_view(), name="auth-email-confirm"),
]
