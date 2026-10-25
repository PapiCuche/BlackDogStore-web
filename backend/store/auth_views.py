import logging
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from rest_framework import generics, permissions, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from rest_framework_simplejwt.serializers import TokenObtainSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .auth_serializers import (
    EMAIL_TAKEN, RegisterSerializer, UserSerializer,
    VerifyEmailSerializer, ResendVerificationSerializer,
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer,
    ChangePasswordSerializer,
)
from . import accounts, auth_sessions, security_log, verification_codes
from .authentication import enforce_csrf
from .emails import send_verification_email, send_password_reset_email
from .models import AccountToken, AuthSession
from .permissions import get_user_role
from .token_revocation import refresh_is_revoked, revoke_access_token, revoke_all_tokens
from .throttles import (
    LoginThrottle, RefreshThrottle, RegisterThrottle,
    ResendVerificationThrottle, VerifyEmailCodeThrottle, PasswordResetRequestThrottle,
    PasswordResetConfirmThrottle, ChangePasswordThrottle,
    SessionKeepaliveThrottle, SessionStatusThrottle,
)

logger = logging.getLogger(__name__)


#: The two rules of the database about who an account is, by the name each
#: carries in the refusal — the index of REGISTER-RACE-01 and Django's own
#: unique username (PostgreSQL names the constraint; SQLite names the column).
_ADDRESS_RULE = 'store_user_email_ci_uniq'
_USERNAME_RULES = ('auth_user_username_key', 'auth_user.username')


def _already_taken(refused, validated) -> dict | None:
    """
    What the registration form answers when its address or its name is taken —
    said again for a request that only found out when the database refused it.

    Decided by WHICH rule refused, read from the refusal itself and never
    quoted: its text carries the address. None when it was neither rule.
    The words are the form's own, on what the form understood (`validated`).
    """
    said = str(refused)
    if _ADDRESS_RULE in said:
        return {'email': [EMAIL_TAKEN]}
    if any(rule in said for rule in _USERNAME_RULES):
        return {'username': [str(User._meta.get_field('username').error_messages['unique'])]}
    # The refusal did not say which (another driver, another wording): look.
    if accounts.with_email(validated.get('email', '')):
        return {'email': [EMAIL_TAKEN]}
    if User.objects.filter(username=validated.get('username', '')).exists():
        return {'username': [str(User._meta.get_field('username').error_messages['unique'])]}
    return None


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterThrottle]
    # AUTH-B: a way in. A dead session's cookie must not stop somebody creating an account.
    dead_session_is_guest = True

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # DECIDED BEFORE THE ACCOUNT EXISTS, and the account is written once, in
        # the state it has to end in. It used to be saved active and switched
        # off afterwards: anything that broke in between — and the invitation
        # look-up could be made to — answered 500 and left an account that
        # worked, on an address nobody had verified (found by the review).
        # Asked whether or not verification is required here: an invitation
        # sent to this mailbox has proved the address either way
        # (`UserProfile.email_verified_at`).
        vouched = _vouched_by_invitation(request, serializer.validated_data['email'])
        needs_verification = settings.REQUIRE_EMAIL_VERIFICATION and not vouched
        issued = None
        try:
            with transaction.atomic():
                user = serializer.save(is_active=not needs_verification)
                if vouched:
                    accounts.mark_email_verified(user)
                if needs_verification:
                    issued = verification_codes.issue(user)
        except IntegrityError as refused:
            # REGISTER-RACE-01. The form asked «is it taken?» a moment before
            # another request saved the same address or the same name. The
            # database refused the second one; this request is told what it
            # would have been told had it arrived a moment later. Nothing of it
            # was written: the transaction above took it all back.
            taken = _already_taken(refused, serializer.validated_data)
            if taken is None:
                # Not one of the two rules about WHO this is. That is a fault
                # of ours, and a polite 400 would hide a registration broken
                # for everybody: it stays an error.
                raise
            return Response(taken, status=status.HTTP_400_BAD_REQUEST)

        if needs_verification:
            # Its row stays even if the mail fails: it is what marks the account
            # as registered and never confirmed.
            if send_verification_email(user, issued.token, code=issued.code, request=request):
                verification_codes.settle(issued)
            return Response(
                {
                    'detail': 'Registro completado. Revisa tu correo para verificar tu cuenta.',
                    'requires_verification': True,
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {
                'detail': 'Registro completado.',
                'requires_verification': False,
                'user': UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


def _submitted(request, field):
    """A field of the request body, whatever shape the body arrived in."""
    data = request.data
    return data.get(field) if hasattr(data, 'get') else None


def _vouched_by_invitation(request, email) -> bool:
    """
    Whether this registration arrives with a staff invitation FOR THIS ADDRESS.

    E-mail verification asks one thing: does this person read this mailbox? An
    invitation link was sent to that mailbox and nowhere else, so holding one
    that still serves has already answered it. Sending a second e-mail to ask
    again is what left invited workers with an account they could neither use
    nor recreate.

    It vouches for its own address and no other, and only while it serves: an
    invented, expired, revoked, replaced or spent link vouches for nothing, and
    the registration is then exactly what it would have been without it.

    IT FAILS CLOSED. Whatever arrives in that field that is not a token of the
    shape ours have is not looked up at all, and a look-up that cannot be
    completed is a «no»: not knowing is never a reason to skip a verification.
    """
    from . import staff_services

    raw_token = _submitted(request, 'invitation_token')
    if not isinstance(raw_token, str) or not _TOKEN_SHAPE.fullmatch(raw_token):
        return False
    try:
        invitation = staff_services.find_invitation(raw_token)
    except Exception:  # noqa: BLE001 — a «no», and said without the token
        logger.exception('an invitation could not be looked up during a registration')
        return False
    return invitation is not None and invitation.email == staff_services.normalize_email(email)


# The only address a recovery e-mail may carry the person back to: an invitation,
# with a token of the shape ours have. Not «any local path»: the link is written
# into an e-mail, and an e-mail is not the place for somebody else's choice of URL.
_TOKEN_SHAPE = re.compile(r'[A-Za-z0-9_-]{16,128}')
_INVITATION_RETURN = re.compile(r'/invitacion\?token=(?P<token>[A-Za-z0-9_-]{16,128})')


def _invitation_return(raw, user):
    """
    `raw` if it is the address of an invitation THAT SERVES AND IS FOR THIS
    ACCOUNT'S ADDRESS; otherwise None.

    The shape alone is not enough: the value is written into an e-mail to
    `user`, and the only thing that belongs there is that person's own
    invitation.
    """
    from . import staff_services

    match = _INVITATION_RETURN.fullmatch(raw) if isinstance(raw, str) else None
    if match is None:
        return None
    try:
        invitation = staff_services.find_invitation(match.group('token'))
    except Exception:  # noqa: BLE001 — the link simply does not carry it
        logger.exception('an invitation could not be looked up for a recovery link')
        return None
    if invitation is None or invitation.email != staff_services.normalize_email(user.email):
        return None
    return raw


def _recoverable_account(email):
    """
    The account a recovery e-mail may be sent for, or None.

    An active one, as always. And one that was registered and never verified:
    the recovery link goes to the same mailbox the verification link went to,
    so using it proves the same thing, and it is the only way such an account
    can ever be finished by somebody who has lost the first e-mail.

    Two accounts under one address is a state this code never creates; a
    database that has it gets the same quiet answer as an unknown address, not
    a guess about which of the two was meant.
    """
    found = [user for user in accounts.with_email(email) if user.is_active or accounts.is_unverified(user)]
    if len(found) > 1:
        logger.warning('password recovery skipped: %d accounts share one address', len(found))
        return None
    return found[0] if found else None


class _CredentialsSerializer(TokenObtainSerializer):
    """
    Username and password, checked exactly as SimpleJWT checks them — and no token.

    The tokens are issued by `auth_sessions.issue_web_session`, with the session's
    `sid` in them. `TokenObtainPairSerializer` would mint a pair without one.
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        if jwt_settings.UPDATE_LAST_LOGIN:
            from django.contrib.auth.models import update_last_login
            update_last_login(None, self.user)
        return data


class LoginView(APIView):
    """Validates credentials and sets JWT tokens in HttpOnly cookies (not response body)."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [LoginThrottle]
    # AUTH-B: a way in. Signing in again over a dead session's cookie must work.
    dead_session_is_guest = True

    def post(self, request):
        serializer = _CredentialsSerializer(data=request.data, context={'request': request})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])
        except AuthenticationFailed:
            security_log.login_failed(
                request, channel='web', identifier=_submitted(request, 'username'),
            )
            raise

        security_log.login_succeeded(request, channel='web', user=serializer.user)
        response = Response({
            'detail': 'Login correcto.',
            'user': UserSerializer(serializer.user).data,
        })
        auth_sessions.issue_web_session(
            response, serializer.user, method=AuthSession.METHOD_PASSWORD, request=request,
        )
        return response


class RefreshView(APIView):
    """
    Reads the refresh cookie, issues a new access cookie and a rotated refresh.

    AUTH-B. Renewing is NEVER activity, and a session that is over does not
    renew: idle, past its absolute limit, ended, or a refresh with no session.
    The answer to that is 401 `session_expired` and both cookies are deleted.

    ONE RENEWAL PER TOKEN. SimpleJWT checks the blacklist and writes it with no
    lock, so two requests with the same refresh could both rotate (ADR H-6). The
    session row is locked first and the blacklist asked again under the lock:
    the second request finds the first one's rotation and is refused — without
    touching the cookies, which by then belong to whoever won.

    CSRF. Like before, it is enforced by the authentication only when a live
    access cookie comes along; with an expired one, what stops a cross-site
    renewal is `SameSite` on the cookies (SEC-SET-03), and a renewal writes
    nothing a third party could use.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [RefreshThrottle]
    # The view answers a dead session itself (with the code the browser needs).
    dead_session_is_guest = True

    def post(self, request):
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if not refresh_cookie:
            return Response(
                {'detail': 'No se encontró el refresh token.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # H4.1.2B — AUTH-REVOCATION-REFRESH-01. `TokenRefreshSerializer` sólo
        # sabe de firma, caducidad y lista negra, así que la pregunta por la
        # revocación global hay que hacerla AQUÍ, antes de que rote nada. Un
        # refresh anterior al cambio de contraseña entregaba un access nuevo y
        # perfectamente válido: la sesión resucitaba por la puerta de atrás.
        try:
            token = RefreshToken(refresh_cookie)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])

        user = User.objects.filter(pk=token.get(jwt_settings.USER_ID_CLAIM)).first()
        sid = token.get(auth_sessions.SID_CLAIM)
        if user is not None and not user.is_active and sid:
            auth_sessions.end_sid(sid, user.pk, AuthSession.END_DEACTIVATED)
        if user is None or not user.is_active or refresh_is_revoked(user, token) or not sid:
            return auth_sessions.expired_response(request)

        # Tighten BEFORE judging: somebody who became staff more than 8 hours
        # after signing in as a customer has no session left to renew.
        if auth_sessions.is_internal_account(user):
            auth_sessions.harden(user)

        with transaction.atomic():
            session = auth_sessions.load(sid, lock=True)
            reason = auth_sessions.verdict(session, user.pk)
            if reason is None:
                try:
                    RefreshToken(refresh_cookie)  # the blacklist, asked again under the lock
                except TokenError:
                    return Response({'detail': 'Sesión expirada.'}, status=status.HTTP_401_UNAUTHORIZED)
                serializer = TokenRefreshSerializer(data={'refresh': refresh_cookie})
                try:
                    serializer.is_valid(raise_exception=True)
                except TokenError as exc:
                    raise InvalidToken(exc.args[0])
                session = auth_sessions.load(sid)
            elif reason:
                auth_sessions.end(session, reason)

        if reason is not None:
            return auth_sessions.expired_response(request)

        data = serializer.validated_data
        response = Response({'detail': 'Token renovado.'})
        auth_sessions.set_cookies(
            response, session,
            data['access'],
            data.get('refresh'),  # present only when ROTATE_REFRESH_TOKENS=True
        )
        return response


class LogoutView(APIView):
    """
    Clears both JWT cookies, ending the session.

    authentication_classes = [] so that CookieJWTAuthentication never runs
    for this endpoint.  This allows logout to succeed even when the access
    token has already expired or is otherwise invalid.  CSRF is enforced
    manually in post() instead.

    CSRF enforcement: if any auth cookie is present in the request, a valid
    X-CSRFToken header is required.  This prevents logout-CSRF attacks where
    an attacker forces an authenticated user to log out.  When no auth cookies
    are present (already logged out or expired) the request is allowed through
    unconditionally so the cookie cleanup still runs.

    Blacklisting: the refresh token is blacklisted before clearing cookies so
    it cannot be reused after logout, even within its remaining TTL.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        has_auth_cookie = bool(
            request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME) or
            request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        )
        if has_auth_cookie:
            enforce_csrf(request)  # raises PermissionDenied → 403 on failure

        # Blacklist the refresh token so it cannot be reused after logout.
        # Failures (expired, invalid, already blacklisted) are silenced — cookie
        # clearing must always succeed regardless of token state.
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if refresh_cookie:
            try:
                RefreshToken(refresh_cookie).blacklist()
            except TokenError:
                pass

        # H4.1.2B — AUTH-REVOCATION-01. El access token muere AQUÍ, no cuando
        # caduque. Antes sólo caía el refresh: borrar la cookie deja sin
        # credencial al navegador honrado y no le quita nada a quien ya copió el
        # token, que seguía entrando hasta 30 minutos después de «cerrar sesión».
        access_cookie = request.COOKIES.get(settings.JWT_COOKIE_ACCESS_NAME)
        if access_cookie:
            try:
                token = AccessToken(access_cookie)
                revoke_access_token(
                    User.objects.filter(
                        pk=token.payload.get(jwt_settings.USER_ID_CLAIM),
                    ).first(),
                    token,
                )
            except TokenError:
                pass  # caducado o inválido: ya no abre nada

        # AUTH-B: and the session itself is over, so every token of it dies —
        # the rotated ones too, which no `jti` above names.
        auth_sessions.end_presented(request, AuthSession.END_LOGOUT)

        response = Response({'detail': 'Sesión cerrada.'})
        response.delete_cookie(settings.JWT_COOKIE_ACCESS_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        response.delete_cookie(settings.JWT_COOKIE_REFRESH_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response


class SessionStatusView(APIView):
    """
    GET — how long this session has left. NEVER activity, whatever is sent.

    Seconds, not dates: the browser adds them to the instant it received them,
    so a wrong clock on the computer moves nothing.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SessionStatusThrottle]

    def get(self, request):
        session = auth_sessions.current(request)
        if session is None:
            return auth_sessions.expired_response(request)
        response = Response(auth_sessions.status(session))
        response['Cache-Control'] = 'no-store'
        return response


class SessionKeepaliveView(APIView):
    """POST — «Continuar sesión». Activity by definition: only a person presses it."""
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SessionKeepaliveThrottle]

    def post(self, request):
        session = auth_sessions.current(request)
        if session is None:
            return auth_sessions.expired_response(request)
        renewed = auth_sessions.touch(session)
        request._request.bd_activity_done = True
        response = Response(auth_sessions.status(session, renewed=renewed))
        response['Cache-Control'] = 'no-store'
        return response


class CsrfView(APIView):
    """Sets the csrftoken cookie so the frontend can read it for X-CSRFToken headers."""
    permission_classes = [permissions.AllowAny]
    # AUTH-B: a way in. Renewing and signing in need the CSRF cookie first.
    dead_session_is_guest = True

    def get(self, request):
        get_token(request)  # populates csrftoken cookie (not HttpOnly)
        return Response({'detail': 'CSRF cookie configurado.'})


class UserDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        data = UserSerializer(user).data
        data['role'] = get_user_role(user)
        data['is_staff'] = user.is_staff or user.is_superuser
        # CLIENT-SEC-01, an added field: when the mailbox was PROVEN, or null.
        # Not «active»: an account is active wherever verification is not asked.
        data['email_verified_at'] = accounts.email_verified_at(user)
        return Response(data)


class VerifyEmailView(APIView):
    """
    POST with {token} — activates the user account associated with the email verification token.
    The token is single-use and expires in 24 hours.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token']

        try:
            account_token = AccountToken.consume(raw_token, AccountToken.PURPOSE_EMAIL_VERIFICATION)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = account_token.user
        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])
        accounts.mark_email_verified(user)

        return Response({'detail': 'Correo verificado correctamente. Ya puedes iniciar sesión.'})


class VerifyEmailCodeView(APIView):
    """
    POST with {email, code} — the 6-digit code of the verification e-mail.

    The link (`VerifyEmailView`) stays the main way. ONE answer for every
    refusal: it never says whether the account exists, whether the code was
    close, or which rule refused it. The rules are in `verification_codes`.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [VerifyEmailCodeThrottle]

    _REFUSED = {'detail': 'El código no es válido o ya venció. Pide uno nuevo.'}

    def post(self, request):
        data = request.data if hasattr(request.data, 'get') else {}
        user = verification_codes.check(
            data.get('email'), data.get('code'),
            on_exhausted=lambda account: security_log.verify_code_exhausted(request, user=account),
        )
        if user is None:
            return Response(self._REFUSED, status=status.HTTP_400_BAD_REQUEST)
        return Response({'detail': 'Correo verificado correctamente. Ya puedes iniciar sesión.'})


class ResendVerificationView(APIView):
    """
    POST with {email} — resends the verification email.
    Always returns a generic message (anti-enumeration).
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ResendVerificationThrottle]

    _GENERIC_RESPONSE = {'detail': 'Si el correo existe y no está verificado, recibirás un nuevo enlace.'}

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        # Registered and never confirmed — not «inactive for whatever reason»: an
        # account somebody switched off is not sent a way to switch itself on.
        waiting = [user for user in accounts.with_email(email) if accounts.is_unverified(user)]
        if len(waiting) != 1:
            # None, or a legacy duplicate: the same quiet answer, not a guess.
            return Response(self._GENERIC_RESPONSE)
        user = waiting[0]

        # None when this account was sent one less than a minute ago, or has had
        # its codes for the day. Whoever asked is told the same either way.
        issued = verification_codes.issue(user)
        if issued is not None:
            if send_verification_email(user, issued.token, code=issued.code, request=request):
                verification_codes.settle(issued)
            else:
                # Nobody received it: the wait, the day and the previous code stay as they were.
                verification_codes.withdraw(issued)
        return Response(self._GENERIC_RESPONSE)


class PasswordResetRequestView(APIView):
    """
    POST with {email[, next]} — sends a password reset link to the user's email.
    Always returns a generic message (anti-enumeration).

    Sent to an active account, and to one that was registered and never
    verified (`_recoverable_account`). `next` travels in the link only when it
    is the address of a staff invitation.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRequestThrottle]

    _GENERIC_RESPONSE = {'detail': 'Si el correo existe, enviaremos instrucciones para restablecer tu contraseña.'}

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        user = _recoverable_account(email)
        if user is None:
            return Response(self._GENERIC_RESPONSE)

        next_path = _invitation_return(serializer.validated_data.get('next'), user)

        raw_token, _ = AccountToken.make(user, AccountToken.PURPOSE_PASSWORD_RESET, ttl_hours=1)
        send_password_reset_email(user, raw_token, next_path=next_path, request=request)
        return Response(self._GENERIC_RESPONSE)


class PasswordResetConfirmView(APIView):
    """
    POST with {token, new_password} — validates the reset token, changes the password,
    blacklists any active refresh token, and clears auth cookies.
    Does not auto-login after reset.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetConfirmThrottle]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        try:
            account_token = AccountToken.consume(raw_token, AccountToken.PURPOSE_PASSWORD_RESET)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        user = account_token.user
        # Asked BEFORE anything changes: an account registered and never
        # verified is finished here. The link was read in its mailbox, which is
        # all verification ever asked, and the new password replaces whatever
        # was typed when the account was made — by whoever made it.
        finishing = accounts.is_unverified(user)
        user.set_password(new_password)
        with transaction.atomic():
            if finishing:
                # Verified for good: it must not look «never verified» again if
                # somebody switches it off one day.
                user.is_active = True
                accounts.mark_verified(user)
            # EVERY consumed recovery link proves the mailbox, whether it
            # finished an account or served one that was already active: the
            # link was sent there and nowhere else (CLIENT-SEC-01 · F7).
            accounts.mark_email_verified(user)
            user.save(update_fields=['password', 'is_active'] if finishing else ['password'])

        # H4.1.2B — restablecer la contraseña cierra TODAS las sesiones, que es
        # lo que esta pantalla promete y lo que espera quien la usa porque cree
        # que alguien entró en su cuenta.
        revoke_all_tokens(user)

        # Blacklist any active refresh token from this session
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if refresh_cookie:
            try:
                RefreshToken(refresh_cookie).blacklist()
            except TokenError:
                pass

        response = Response({
            'detail': 'Contraseña restablecida. Inicia sesión con tu nueva contraseña.',
            # The name to log in with. Whoever got this far has read the
            # account's mailbox, and somebody who never chose a password — a
            # Google account, an invited worker — may not know they have one.
            'username': user.get_username(),
        })
        response.delete_cookie(settings.JWT_COOKIE_ACCESS_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        response.delete_cookie(settings.JWT_COOKIE_REFRESH_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response


class ChangePasswordView(APIView):
    """
    POST with {current_password, new_password} — changes the authenticated user's password.
    Requires valid access cookie + CSRF (enforced automatically by CookieJWTAuthentication).
    Blacklists the current refresh token and clears cookies — user must re-login.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ChangePasswordThrottle]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current_password = serializer.validated_data['current_password']
        new_password = serializer.validated_data['new_password']

        if not request.user.check_password(current_password):
            return Response(
                {'detail': 'Contraseña actual incorrecta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])

        # H4.1.2B — «todas las sesiones quedan invalidadas» ahora es cierto: el
        # sello del perfil invalida también los access tokens ya emitidos.
        revoke_all_tokens(request.user)

        # Blacklist the current refresh token — all sessions are invalidated
        refresh_cookie = request.COOKIES.get(settings.JWT_COOKIE_REFRESH_NAME)
        if refresh_cookie:
            try:
                RefreshToken(refresh_cookie).blacklist()
            except TokenError:
                pass

        response = Response({'detail': 'Contraseña cambiada. Por favor, inicia sesión nuevamente.'})
        response.delete_cookie(settings.JWT_COOKIE_ACCESS_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        response.delete_cookie(settings.JWT_COOKIE_REFRESH_NAME, path='/', samesite=settings.JWT_COOKIE_SAMESITE)
        return response
