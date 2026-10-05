"""
GOOGLE-AUTH — «Continuar con Google», the HTTP side.

    THE SESSION IS THE USUAL ONE.

A successful sign-in ends exactly like a password login: the same HttpOnly
cookies, the same refresh rotation, the same revocation. The Google token is
read once, here, and discarded.

    AN E-MAIL THAT ALREADY HAS AN ACCOUNT IS NEVER LINKED BY ITSELF.

`User.email` is not verified by default in this platform (and is not unique),
so "Google says this is ana@…" is not proof that the person is the owner of the
account that says ana@…. They get a 409 and are asked for that account's
password; only then is the Google identity attached to it.
"""
from __future__ import annotations

import re
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from . import google_identity as google
from . import security_log
from .auth_views import _set_auth_cookies
from .models import ExternalIdentity
from .auth_serializers import UserSerializer
from .throttles import GoogleSignInThrottle

User = get_user_model()

REFUSED = 'No se pudo verificar la cuenta de Google.'
NOT_LINKED = 'No se pudo vincular la cuenta. Revisa la contraseña.'
COOKIE_PATH = '/api/auth/google'


def _username_for(email: str) -> str:
    """A unique username nobody chose: the local part, with a suffix when taken."""
    base = re.sub(r'[^a-z0-9._-]', '', email.split('@')[0].lower())[:24] or 'usuario'
    candidate = base
    while User.objects.filter(username=candidate).exists():
        candidate = f'{base}-{secrets.token_hex(3)}'
    return candidate


class _GoogleMixin:
    # No session is read: this is how one begins.
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [GoogleSignInThrottle]

    def initial(self, request, *args, **kwargs):
        if not google.is_enabled():
            # Not configured here: the endpoint does not exist.
            raise Http404()
        super().initial(request, *args, **kwargs)

    def identity(self, request):
        credential = request.data.get('credential') if hasattr(request.data, 'get') else None
        return google.verify(
            credential, browser_nonce=request.COOKIES.get(google.NONCE_COOKIE, ''),
        )

    def refused(self, request, identifier='-'):
        security_log.login_failed(request, channel='google', identifier=identifier)
        return Response({'detail': REFUSED}, status=status.HTTP_400_BAD_REQUEST)

    def session(self, request, user, proven, *, created: bool):
        """Issue the cookie session. The attempt is spent here, and only here."""
        if not user.is_active:
            security_log.login_failed(request, channel='google', identifier=proven.email)
            return Response(
                {'detail': 'Esta cuenta está desactivada.'}, status=status.HTTP_403_FORBIDDEN,
            )
        if not google.consume_nonce(proven.nonce):
            return self.refused(request, proven.email)
        ExternalIdentity.objects.filter(
            provider=ExternalIdentity.PROVIDER_GOOGLE, subject=proven.subject,
        ).update(last_login_at=timezone.now())
        security_log.login_succeeded(request, channel='google', user=user)
        refresh = RefreshToken.for_user(user)
        response = Response({
            'detail': 'Login correcto.', 'created': created, 'user': UserSerializer(user).data,
        })
        _set_auth_cookies(response, str(refresh.access_token), str(refresh))
        response.delete_cookie(google.NONCE_COOKIE, path=COOKIE_PATH)
        return response


class GoogleConfigView(APIView):
    """
    GET — whether this site offers Google, with what the button needs to start.

    Sets the attempt's nonce as an HttpOnly cookie as well as returning it: the
    page gives it to Google, Google puts it in the token, and the browser has
    to present the same value back. A token obtained anywhere else has no
    matching cookie here.
    """

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [GoogleSignInThrottle]

    def get(self, request):
        if not google.is_enabled():
            return Response({'enabled': False})
        nonce = google.issue_nonce()
        response = Response({'enabled': True, 'client_id': google.client_id(), 'nonce': nonce})
        response.set_cookie(
            google.NONCE_COOKIE, nonce, max_age=google.NONCE_MAX_AGE, httponly=True,
            secure=settings.JWT_COOKIE_SECURE, samesite='Lax', path=COOKIE_PATH,
        )
        response['Cache-Control'] = 'no-store'
        return response


class GoogleSignInView(_GoogleMixin, APIView):
    """POST {credential} — sign in, or create the account on a first visit."""

    def post(self, request):
        try:
            proven = self.identity(request)
        except google.GoogleIdentityError:
            return self.refused(request)

        linked = ExternalIdentity.objects.select_related('user').filter(
            provider=ExternalIdentity.PROVIDER_GOOGLE, subject=proven.subject,
        ).first()
        if linked is not None:
            return self.session(request, linked.user, proven, created=False)

        if User.objects.filter(email__iexact=proven.email).exists():
            return Response(
                {
                    'code': 'link_required',
                    'detail': 'Ya existe una cuenta con este correo. Confirma su contraseña '
                              'para entrar con Google a partir de ahora.',
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                user = User(
                    username=_username_for(proven.email), email=proven.email,
                    first_name=proven.first_name, last_name=proven.last_name,
                )
                # No password exists, and none is invented: this account is
                # entered with Google until its owner sets one.
                user.set_unusable_password()
                user.save()
                ExternalIdentity.objects.create(
                    user=user, provider=ExternalIdentity.PROVIDER_GOOGLE,
                    subject=proven.subject, email_at_link=proven.email,
                )
        except IntegrityError:
            # Two tabs finishing the same first sign-in: one created it.
            linked = ExternalIdentity.objects.select_related('user').filter(
                provider=ExternalIdentity.PROVIDER_GOOGLE, subject=proven.subject,
            ).first()
            if linked is None:
                return self.refused(request, proven.email)
            return self.session(request, linked.user, proven, created=False)
        return self.session(request, user, proven, created=True)


class GoogleLinkView(_GoogleMixin, APIView):
    """
    POST {credential, password} — attach Google to an account that already
    exists, by proving its password, and sign in.

    One answer for every failure — no such account, two accounts with that
    address, an account without a password, a wrong password — so the endpoint
    cannot be used to learn which addresses are registered.
    """

    def post(self, request):
        try:
            proven = self.identity(request)
        except google.GoogleIdentityError:
            return self.refused(request)

        linked = ExternalIdentity.objects.select_related('user').filter(
            provider=ExternalIdentity.PROVIDER_GOOGLE, subject=proven.subject,
        ).first()
        if linked is not None:
            # Already somebody's: Google opens THAT account, whatever was typed.
            return self.session(request, linked.user, proven, created=False)

        password = request.data.get('password') if hasattr(request.data, 'get') else None
        password = password if isinstance(password, str) else ''
        candidates = list(User.objects.filter(email__iexact=proven.email)[:2])
        user = candidates[0] if len(candidates) == 1 else None

        ok = False
        if user is not None and user.is_active and user.has_usable_password():
            ok = user.check_password(password)
        else:
            # Spend the same effort when there is nobody to check against.
            User().set_password(password)
        if not ok or ExternalIdentity.objects.filter(
            user=user, provider=ExternalIdentity.PROVIDER_GOOGLE,
        ).exists():
            security_log.login_failed(request, channel='google-link', identifier=proven.email)
            return Response({'detail': NOT_LINKED}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            ExternalIdentity.objects.create(
                user=user, provider=ExternalIdentity.PROVIDER_GOOGLE,
                subject=proven.subject, email_at_link=proven.email,
            )
        except IntegrityError:
            return Response({'detail': NOT_LINKED}, status=status.HTTP_401_UNAUTHORIZED)
        return self.session(request, user, proven, created=False)
