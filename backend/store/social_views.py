"""
SOCIAL-AUTH-01 — «Continuar con Facebook / Apple», the HTTP side, and the
linked accounts of «Mi cuenta».

    GET  /api/auth/social/config/                which providers this site offers
    GET  /api/auth/<facebook|apple>/start/       → the provider (302)
    GET  /api/auth/facebook/callback/            → the storefront (302)
    POST /api/auth/apple/callback/               Apple's form_post → GET below (303)
    GET  /api/auth/apple/callback/               → the storefront (302)
    POST /api/auth/social/<provider>/link/       {password} → {authorize_url}
    GET  /api/auth/social/identities/            what this account has linked
    DELETE /api/auth/social/identities/<p>/      unlink, unless it is the last way in

A callback ends exactly like a password login — the same HttpOnly cookies, the
same refresh rotation and revocation — and lands on the storefront, which says
what happened from the query string (`social=` or `social_error=`). Nothing of
the provider is returned, stored or logged.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from rest_framework import permissions, status
from rest_framework.parsers import FormParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from . import security_log
from . import social_identity as social
from .auth_views import _set_auth_cookies
from .models import ExternalIdentity
from .throttles import SocialLinkThrottle, SocialSignInThrottle
from .token_revocation import token_is_revoked

User = get_user_model()


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


def _adapter_and_config(provider_id):
    """The adapter and its configuration in force, or 404: an unconfigured provider has no endpoints."""
    adapter = social.redirect_provider(provider_id)
    conf = adapter.config() if adapter is not None else None
    if conf is None:
        raise Http404()
    return adapter, conf


class SocialConfigView(APIView):
    """GET — `{providers: {google|facebook|apple: {enabled}}}`. Nothing of any configuration."""

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [SocialSignInThrottle]

    def get(self, request):
        return _no_store(Response({
            'providers': {p: {'enabled': social.is_enabled(p)} for p in social.PROVIDERS},
        }))


class SocialStartView(APIView):
    """GET ?next=/path — opens an attempt for this browser and sends it to the provider."""

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [SocialSignInThrottle]
    provider = ''

    def get(self, request):
        adapter, conf = _adapter_and_config(self.provider)
        begun = social.begin(self.provider, next_path=social.safe_next(request.query_params.get('next')))
        response = HttpResponseRedirect(adapter.authorize_url(conf, begun))
        social.set_attempt_cookie(response, begun)
        return _no_store(response)


def _signed_in_user(request):
    """The account of this browser's cookie session, or None. Read-only: a GET carries no CSRF token."""
    raw = request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME)
    if not raw:
        return None
    try:
        token = AccessToken(raw)
    except TokenError:
        return None
    user = User.objects.filter(pk=token.get(jwt_settings.USER_ID_CLAIM)).first()
    if user is None or not user.is_active or token_is_revoked(user, token):
        return None
    return user


class SocialCallbackView(APIView):
    """GET — where the provider sends the browser back. Finishes the attempt, once."""

    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [SocialSignInThrottle]
    provider = ''

    # -- landing on the storefront ----------------------------------------------------

    def land(self, url):
        response = HttpResponseRedirect(url)
        social.clear_attempt_cookie(response)
        return _no_store(response)

    def refused(self, request, attempt=None, code=social.REFUSED):
        if code != social.CANCELLED:
            security_log.login_failed(request, channel=self.provider, identifier='-')
        if attempt is not None and attempt.intent == social.INTENT_LINK:
            return self.land(social.landing('/account', link_error=code, provider=self.provider))
        return self.land(social.landing('/auth', social_error=code, provider=self.provider))

    # -- the callback -------------------------------------------------------------------

    def get(self, request):
        adapter, conf = _adapter_and_config(self.provider)
        try:
            attempt = social.finish(
                self.provider, request.query_params.get('state'),
                request.COOKIES.get(social.ATTEMPT_COOKIE, ''),
            )
        except social.SocialAuthError:
            return self.refused(request)

        returned = self.returned_for(request, attempt)
        if returned is None:
            return self.refused(request, attempt)
        if returned.get('error'):
            return self.refused(request, attempt, social.CANCELLED)
        try:
            identity = adapter.identify(conf, code=returned.get('code'), attempt=attempt)
        except social.SocialAuthError:
            return self.refused(request, attempt)

        if attempt.intent == social.INTENT_LINK:
            return self.linked(request, attempt, identity)
        return self.signed_in(request, attempt, identity)

    def returned_for(self, request, attempt):
        """What the provider sent back: the query string here; Apple's handed-over POST below."""
        return request.query_params

    def signed_in(self, request, attempt, identity):
        outcome = social.sign_in(identity)
        if outcome.error:
            return self.refused(request, attempt, outcome.error)
        social.touch(identity)
        security_log.login_succeeded(request, channel=self.provider, user=outcome.user)
        refresh = RefreshToken.for_user(outcome.user)
        response = self.land(social.landing('/auth', social=self.provider, next=attempt.next_path))
        _set_auth_cookies(response, str(refresh.access_token), str(refresh))
        return response

    def linked(self, request, attempt, identity):
        # The attempt was opened by a signed-in account that typed its password.
        # It is finished for that account only, and only while it is still the
        # one signed in in this browser.
        user = _signed_in_user(request)
        if user is None or user.pk != attempt.user_id:
            return self.refused(request, attempt)
        reason = social.link(user, identity)
        if reason:
            return self.refused(request, attempt, reason)
        security_log.login_succeeded(request, channel=f'{self.provider}-link', user=user)
        return self.land(social.landing('/account', linked=self.provider))


class AppleCallbackView(SocialCallbackView):
    """
    POST — Apple's `form_post`, from appleid.apple.com.

    A cross-site POST: the browser does not send SameSite=Lax cookies with it,
    so nothing can be decided here. What was posted is handed to the attempt it
    names and the browser is sent (303) to a GET of this same address on this
    site, which carries the cookies and finishes the attempt. Exempt from CSRF
    as every API view is (DRF; no session cookie is read here): the attempt's
    state and the browser binding are what protect it.
    """

    parser_classes = [FormParser]
    provider = 'apple'

    def post(self, request):
        _adapter_and_config(self.provider)
        data = request.data
        state = data.get('state')
        posted = {
            name: data.get(name)[:4096] for name in ('code', 'user', 'error')
            if isinstance(data.get(name), str)
        }
        if not social.hand_over(self.provider, state, posted):
            return self.refused(request)
        response = HttpResponseRedirect(f'{social.redirect_uri(self.provider)}?{urlencode({"state": state})}')
        response.status_code = status.HTTP_303_SEE_OTHER
        return _no_store(response)

    def returned_for(self, request, attempt):
        return attempt.posted


class SocialLinkStartView(APIView):
    """
    POST {password} — link a provider to the signed-in account (from «Mi cuenta»).

    Re-authentication is the account's current password. An account without one
    (made with a provider) sets one first through «¿Olvidaste tu contraseña?».
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SocialLinkThrottle]

    def post(self, request, provider):
        if provider not in social.REDIRECT_PROVIDERS:
            raise Http404()
        adapter, conf = _adapter_and_config(provider)
        user = request.user
        if social.is_privileged(user):
            return Response(
                {'code': social.NOT_ALLOWED,
                 'detail': 'Las cuentas del equipo entran con usuario y contraseña.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if ExternalIdentity.objects.filter(user=user, provider=provider).exists():
            return Response(
                {'code': social.ALREADY_LINKED, 'detail': f'Tu cuenta ya tiene {social.LABELS[provider]} vinculado.'},
                status=status.HTTP_409_CONFLICT,
            )
        if not user.has_usable_password():
            return Response(
                {'code': 'password_required',
                 'detail': 'Para vincular otra forma de entrar, primero crea una contraseña con '
                           '«¿Olvidaste tu contraseña?».'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        password = request.data.get('password') if hasattr(request.data, 'get') else None
        if not isinstance(password, str) or not user.check_password(password):
            security_log.login_failed(request, channel=f'{provider}-link', identifier=user.get_username())
            return Response(
                {'code': 'password', 'detail': 'La contraseña no es correcta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        begun = social.begin(provider, intent=social.INTENT_LINK, user_id=user.pk)
        response = Response({'authorize_url': adapter.authorize_url(conf, begun)})
        social.set_attempt_cookie(response, begun)
        return _no_store(response)


def _ways_in(user) -> int:
    return int(user.has_usable_password()) + ExternalIdentity.objects.filter(user=user).count()


class SocialIdentitiesView(APIView):
    """GET — the providers this account has linked, and which this site offers."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        linked = {row.provider: row for row in ExternalIdentity.objects.filter(user=user)}
        return _no_store(Response({
            'has_password': user.has_usable_password(),
            'email_is_private_relay': social.is_private_relay(user.email),
            'providers': [
                {
                    'provider': provider, 'label': social.LABELS[provider],
                    'enabled': social.is_enabled(provider),
                    'linkable': provider in social.REDIRECT_PROVIDERS,
                    'linked': provider in linked,
                    'linked_at': linked[provider].created_at.isoformat() if provider in linked else None,
                }
                for provider in social.PROVIDERS
            ],
        }))


class SocialIdentityView(APIView):
    """DELETE — unlink one provider. Never the last way into the account."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SocialLinkThrottle]

    def delete(self, request, provider):
        if provider not in social.PROVIDERS:
            raise Http404()
        with transaction.atomic():
            # Two unlinks at once must not both see «there is another way in».
            user = User.objects.select_for_update().get(pk=request.user.pk)
            identity = ExternalIdentity.objects.filter(user=user, provider=provider).first()
            if identity is None:
                raise Http404()
            if _ways_in(user) <= 1:
                return Response(
                    {'code': 'last_way_in',
                     'detail': 'Es tu única forma de entrar. Crea una contraseña antes de desvincularlo.'},
                    status=status.HTTP_409_CONFLICT,
                )
            identity.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
