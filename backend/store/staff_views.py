"""
La superficie interna de altas de personal.

QUÉ SALE Y QUÉ NO
-----------------
El token NO sale nunca en una respuesta. Sale por correo, una vez, hacia el
buzón de la persona invitada. Devolverlo al administrador le daría un enlace de
acceso a una cuenta que no es suya — y lo dejaría en el historial del navegador,
en un registro de red y en cualquier captura de pantalla.

En desarrollo hay una excepción explícita y acotada; ver `_debug_link`.

EL BACKEND MANDA
----------------
Las acciones se ofrecen o no según el estado, pero cada una vuelve a comprobar
autoridad y tenant. Ocultar un botón nunca impidió que alguien llame a la ruta.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .access_views import _company_or_none, _deny_authority, _deny_not_found
from .tenancy import can_delegate_capabilities, has_capability
from .models import AdminAuditLog, StaffInvitation
from .permissions import HasCompanyMembership
from .staff_services import (
    StaffConflict, StaffError, StaffIdentityError, accept_invitation,
    create_invitation, find_invitation, requires_authentication,
    resend_invitation, revoke_invitation,
)
from .throttles import StaffInviteThrottle, StaffAcceptThrottle

logger = logging.getLogger(__name__)

#: Se reutilizan las capacidades que ya existen. Dar de alta a alguien ES
#: administrar personal: no hace falta una autoridad nueva para expresarlo.
CAP_STAFF_MANAGE = 'memberships.manage'
CAP_STAFF_VIEW = 'memberships.view'


def _debug_link(raw_token: str) -> dict:
    """
    El enlace en claro, SÓLO en desarrollo y sólo si no hay correo real.

    Sin esto, montar el entorno de desarrollo exigiría un servidor SMTP para
    poder dar de alta a nadie. Con `DEBUG=False` devuelve un diccionario vacío:
    la rama no existe en producción y no hay bandera que la encienda.
    """
    if not settings.DEBUG or not raw_token:
        return {}
    return {'debug_invitation_token': raw_token}


def invitation_payload(invitation: StaffInvitation) -> dict:
    """
    Los datos de una invitación. NUNCA el token ni su hash.

    El estado que se sirve es `display_status`, que ya tiene la caducidad
    calculada: una pantalla que comparase fechas por su cuenta acabaría
    mostrando «pendiente» sobre una invitación muerta.
    """
    return {
        'id': invitation.pk,
        'email': invitation.email,
        'first_name': invitation.first_name,
        'last_name': invitation.last_name,
        'full_name': invitation.full_name,
        'role_id': invitation.role_id,
        'role_name': invitation.role.name if invitation.role_id else '',
        'area_id': invitation.area_id,
        'area_name': invitation.area.name if invitation.area_id else '',
        'branch_access_mode': invitation.branch_access_mode,
        'branch_ids': invitation.branch_ids or [],
        'status': invitation.display_status,
        'invited_by': (
            invitation.invited_by.get_full_name() or invitation.invited_by.username
            if invitation.invited_by_id else ''
        ),
        'created_at': invitation.created_at,
        'expires_at': invitation.expires_at,
        'accepted_at': invitation.accepted_at,
        'is_usable': invitation.is_usable,
    }


class AdminStaffInvitationListView(APIView):
    """
    GET  /api/admin/staff/invitations/  — las invitaciones de una empresa.
    POST /api/admin/staff/invitations/ — invitar a alguien.

    Invitar NO crea acceso: mientras nadie acepte, la persona no puede entrar.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]

    def get_throttles(self):
        return [StaffInviteThrottle()] if self.request.method == 'POST' \
            else [StaffAcceptThrottle()]

    def get(self, request):
        company = _company_or_none(request, request.query_params.get('company'))
        if not company:
            return _deny_not_found()
        if not has_capability(request.user, company, CAP_STAFF_VIEW):
            return _deny_authority()

        queryset = (
            StaffInvitation.objects.filter(company=company)
            .select_related('role', 'area', 'invited_by')
        )
        estado = (request.query_params.get('status') or '').strip()
        if estado == 'pending':
            # «Pendiente» significa utilizable: una caducada no se ofrece para
            # reenviar como si siguiera viva.
            queryset = [i for i in queryset if i.is_usable]
        else:
            queryset = list(queryset)

        return Response({
            'results': [invitation_payload(i) for i in queryset],
            'count': len(queryset),
        })

    def post(self, request):
        company = _company_or_none(
            request, request.data.get('company') or request.query_params.get('company'),
        )
        if not company:
            return _deny_not_found()
        if not has_capability(request.user, company, CAP_STAFF_MANAGE):
            return _deny_authority()

        # NO SE DELEGA LO QUE NO SE TIENE.
        #
        # Un administrador de empresa no puede invitar a alguien con un rol que
        # contenga capacidades que él mismo no puede conceder. Sin esto, el alta
        # sería una vía de escalada: invito a un cómplice como administrador,
        # acepta, y ya hay alguien con más autoridad que quien lo metió.
        #
        # La comprobación usa el mecanismo que ya existe para esto; no se
        # inventa una regla paralela que pudiera divergir de la otra.
        from .models import CompanyRole

        rol = CompanyRole.objects.filter(
            company=company, pk=request.data.get('role'), is_active=True,
        ).first()
        if rol is not None and not can_delegate_capabilities(
            request.user, company, rol.capabilities or [],
        ):
            return Response(
                {'detail': 'No puedes conceder un rol con capacidades que tú '
                           'mismo no tienes.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        data = request.data
        try:
            invitation, raw, created = create_invitation(
                company=company,
                email=data.get('email', ''),
                first_name=(data.get('first_name') or '').strip(),
                last_name=(data.get('last_name') or '').strip(),
                role_id=data.get('role'),
                area_id=data.get('area') or None,
                branch_access_mode=data.get('branch_access_mode', 'all'),
                branch_ids=data.get('branch_ids') or [],
                invited_by=request.user,
            )
        except StaffConflict as exc:
            # 409 y no 400: no hay nada malformado, es que ya está dentro. Y es
            # información de la propia empresa de quien pregunta.
            return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
        except StaffError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        if created:
            AdminAuditLog.log(
                actor=request.user, action='staff_invitation_created',
                target_type='staff_invitation', target_id=invitation.pk,
                metadata={
                    'invitation_id': invitation.pk,
                    'company_id': company.pk,
                    'email': invitation.email,
                    'role_id': invitation.role_id,
                    'area_id': invitation.area_id,
                    # NUNCA el token. Ni en claro ni su hash: una bitácora que
                    # lo guardara sería una copia del enlace de acceso.
                },
                request=request, company=company,
            )
            _send_invitation_email(invitation, raw)

        return Response(
            {**invitation_payload(invitation), **_debug_link(raw)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminStaffInvitationDetailView(APIView):
    """POST .../resend/ y .../revoke/ — acciones deliberadas sobre una invitación."""

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [StaffInviteThrottle]

    def _scoped(self, request, pk):
        """
        La invitación, dentro del tenant de quien llama.

        Una invitación de otra empresa responde igual que una inexistente: un
        403 confirmaría que ese identificador existe.
        """
        company = _company_or_none(
            request, request.data.get('company') or request.query_params.get('company'),
        )
        if not company:
            return None, None, _deny_not_found()
        if not has_capability(request.user, company, CAP_STAFF_MANAGE):
            return None, None, _deny_authority()
        invitation = (
            StaffInvitation.objects.select_related('role', 'area', 'company')
            .filter(company=company, pk=pk).first()
        )
        if invitation is None:
            return None, None, _deny_not_found()
        return company, invitation, None

    def post(self, request, pk, action):
        company, invitation, error = self._scoped(request, pk)
        if error:
            return error

        try:
            if action == 'resend':
                invitation, raw = resend_invitation(invitation)
                evento = 'staff_invitation_resent'
                _send_invitation_email(invitation, raw)
                extra = _debug_link(raw)
            elif action == 'revoke':
                invitation = revoke_invitation(invitation)
                evento, raw, extra = 'staff_invitation_revoked', '', {}
            else:
                return Response(
                    {'detail': 'Acción no reconocida.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except StaffError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action=evento,
            target_type='staff_invitation', target_id=invitation.pk,
            metadata={'invitation_id': invitation.pk, 'company_id': company.pk,
                      'email': invitation.email},
            request=request, company=company,
        )
        return Response({**invitation_payload(invitation), **extra})


class StaffInvitationAcceptView(APIView):
    """
    GET  /api/staff/invitations/accept/  — qué invitación es ésta.
    POST /api/staff/invitations/accept/ — aceptarla.

    RUTA PÚBLICA PARA LEER, AUTENTICADA PARA ACEPTAR. La lectura sólo devuelve
    el nombre de la empresa y si hace falta iniciar sesión: sin eso la persona
    no sabría a qué la invitan ni qué hacer a continuación.

    Lo que NO devuelve: quién más trabaja allí, qué permisos concede, ni si la
    cuenta existe en otras empresas.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [StaffAcceptThrottle]

    def _invitation(self, request):
        token = (
            request.data.get('token') if request.method == 'POST'
            else request.query_params.get('token')
        )
        return find_invitation(token)

    def get(self, request):
        invitation = self._invitation(request)
        if invitation is None:
            # UNA SOLA RESPUESTA para inexistente, alterado, caducado, revocado
            # y usado. Distinguirlos diría a quien prueba tokens si acertó el
            # formato o sólo el plazo.
            return Response(
                {'detail': 'Esta invitación no es válida o ya expiró.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({
            'company_name': invitation.company.name,
            'email': invitation.email,
            'first_name': invitation.first_name,
            'last_name': invitation.last_name,
            'role_name': invitation.role.name if invitation.role_id else '',
            'area_name': invitation.area.name if invitation.area_id else '',
            'expires_at': invitation.expires_at,
            # Le dice a la pantalla qué pedir: iniciar sesión o crear la cuenta.
            'requires_authentication': requires_authentication(invitation),
        })

    def post(self, request):
        invitation = self._invitation(request)
        if invitation is None:
            return Response(
                {'detail': 'Esta invitación no es válida o ya expiró.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            with transaction.atomic():
                membership = accept_invitation(invitation, request.user)
        except StaffIdentityError as exc:
            # 401 y no 403: lo que falta es demostrar quién eres.
            return Response(
                {'detail': str(exc)}, status=status.HTTP_401_UNAUTHORIZED,
            )
        except StaffError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        AdminAuditLog.log(
            actor=request.user, action='staff_invitation_accepted',
            target_type='staff_invitation', target_id=invitation.pk,
            metadata={'invitation_id': invitation.pk,
                      'membership_id': membership.pk,
                      'company_id': membership.company_id},
            request=request, company=membership.company,
        )
        return Response({
            'company_name': membership.company.name,
            'membership_id': membership.pk,
        })


def _send_invitation_email(invitation: StaffInvitation, raw_token: str) -> None:
    """
    Envía el enlace al buzón de la persona.

    EL ENLACE APUNTA AL FRONTEND, que luego intercambia el token con el backend.
    Poner aquí la URL de la API dejaría a la persona ante un JSON.

    Un fallo de correo NO tumba la invitación: la fila ya existe y se puede
    reenviar. Levantar aquí dejaría al administrador sin saber si invitó o no.
    """
    from django.core.mail import send_mail

    base = getattr(settings, 'FRONTEND_URL', '') or ''
    link = f'{base.rstrip("/")}/invitacion?token={raw_token}'
    empresa = invitation.company.name

    try:
        send_mail(
            subject=f'Invitación para unirte a {empresa}',
            message=(
                f'Hola {invitation.first_name or ""},\n\n'
                f'Te han invitado a formar parte del equipo de {empresa}.\n\n'
                f'Configura tu acceso aquí:\n{link}\n\n'
                f'El enlace caduca el '
                f'{invitation.expires_at.strftime("%d/%m/%Y")}.\n\n'
                f'Si no esperabas este correo, puedes ignorarlo.\n'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[invitation.email],
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001
        # Se registra sin el enlace: un registro con el token dentro sería una
        # copia del acceso.
        logger.exception(
            'No se pudo enviar la invitación %s', invitation.pk,
        )


class AdminStaffListView(APIView):
    """
    GET /api/admin/staff/ — el PERSONAL de una empresa, en lenguaje humano.

    POR QUÉ EXISTE ESTA VISTA HABIENDO YA `/admin/memberships/`
    -----------------------------------------------------------
    Aquélla devuelve membresías: `username`, `role`, identificadores. La pantalla
    de Personal necesita personas: nombre completo, correo, sus áreas, sus roles
    y sus sucursales. Componer eso desde el cliente obligaba a una llamada de
    asignaciones POR PERSONA — un N+1 sobre HTTP que crece con la plantilla.

    ES UNA PROYECCIÓN, NO UN SEGUNDO MODELO DE PERMISOS. No decide nada: lee lo
    que ya decidieron `Membership`, `MembershipRoleAssignment` y
    `MembershipBranchAccess`. La autoridad sigue viviendo donde vivía.

    `/admin/memberships/` se conserva intacta: hay código escrito contra ella.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [StaffAcceptThrottle]

    def get(self, request):
        from django.db.models import Prefetch

        from .models import (
            Membership, MembershipBranchAccess, MembershipRoleAssignment,
        )

        company = _company_or_none(request, request.query_params.get('company'))
        if not company:
            return _deny_not_found()
        if not has_capability(request.user, company, CAP_STAFF_VIEW):
            return _deny_authority()

        queryset = (
            Membership.objects.filter(company=company)
            .select_related('user', 'branch')
            # UNA consulta por relación, no una por persona. Es la diferencia
            # entre una plantilla de 200 y 401 peticiones.
            .prefetch_related(
                Prefetch(
                    'role_assignments',
                    queryset=MembershipRoleAssignment.objects
                    .filter(is_active=True).select_related('role', 'area'),
                ),
                Prefetch(
                    'branch_access',
                    queryset=MembershipBranchAccess.objects
                    .filter(is_active=True).select_related('branch'),
                ),
            )
        )

        # --- búsqueda ---
        #
        # SERVIDOR, no navegador. Traer toda la plantilla para filtrarla en el
        # cliente funciona con seis personas y deja de funcionar con seiscientas.
        buscar = (request.query_params.get('search') or '').strip()
        if buscar:
            from django.db.models import Q

            queryset = queryset.filter(
                Q(user__first_name__icontains=buscar)
                | Q(user__last_name__icontains=buscar)
                | Q(user__email__icontains=buscar)
                | Q(user__username__icontains=buscar)
            )

        # --- filtros ---
        estado = (request.query_params.get('status') or '').strip()
        if estado == 'active':
            queryset = queryset.filter(is_active=True)
        elif estado == 'inactive':
            queryset = queryset.filter(is_active=False)

        area_id = request.query_params.get('area')
        if area_id:
            queryset = queryset.filter(
                role_assignments__area_id=area_id,
                role_assignments__is_active=True,
            ).distinct()

        role_id = request.query_params.get('role')
        if role_id:
            queryset = queryset.filter(
                role_assignments__role_id=role_id,
                role_assignments__is_active=True,
            ).distinct()

        branch_id = request.query_params.get('branch')
        if branch_id:
            from django.db.models import Q

            # «Todas las sucursales» ALCANZA a la sucursal filtrada: quien tiene
            # acceso completo también trabaja ahí, y omitirlo daría una lista
            # que miente por defecto.
            queryset = queryset.filter(
                Q(branch_access__branch_id=branch_id,
                  branch_access__is_active=True)
                | Q(branch_access_mode=Membership.ACCESS_MODE_ALL)
            ).distinct()

        personas = [self._person(m) for m in queryset]
        # Se ordena por nombre visible y no por identificador: una lista de
        # personas ordenada por clave primaria no la puede recorrer nadie.
        personas.sort(key=lambda p: (not p['is_active'], p['full_name'].lower()))

        return Response({'results': personas, 'count': len(personas)})

    @staticmethod
    def _person(membership) -> dict:
        """
        Una persona, tal como se lee. El identificador viaja pero no se muestra.

        EL NOMBRE NO ES LA IDENTIDAD: dos personas pueden llamarse igual, y por
        eso el correo va siempre al lado. Cuando no hay nombre se cae al usuario,
        que es feo pero nunca ambiguo.
        """
        usuario = membership.user
        completo = f'{usuario.first_name} {usuario.last_name}'.strip()

        areas, roles = {}, {}
        for asignacion in membership.role_assignments.all():
            if asignacion.role_id:
                roles[asignacion.role_id] = asignacion.role.name
            if asignacion.area_id:
                areas[asignacion.area_id] = asignacion.area.name

        todas = membership.branch_access_mode == membership.ACCESS_MODE_ALL
        sucursales = (
            [] if todas
            else [a.branch.name for a in membership.branch_access.all()]
        )

        return {
            'id': membership.pk,
            'full_name': completo or usuario.username,
            'first_name': usuario.first_name,
            'last_name': usuario.last_name,
            'email': usuario.email,
            'is_active': membership.is_active,
            'areas': [{'id': k, 'name': v} for k, v in areas.items()],
            'roles': [{'id': k, 'name': v} for k, v in roles.items()],
            'branch_access_mode': membership.branch_access_mode,
            'branches': sucursales,
            'branch_scope_label': (
                'Todas las sucursales' if todas
                else (', '.join(sucursales) or 'Sin sucursales asignadas')
            ),
        }


class AdminStaffMembershipView(APIView):
    """
    PATCH /api/admin/staff/{pk}/ — desactivar o reactivar a alguien.

    DESACTIVAR NO ES BORRAR. La fila se conserva porque es lo que sostiene el
    historial de ventas, reparaciones y auditoría de esa persona en esta
    empresa. Borrar la identidad global sería además borrar sus compras como
    cliente, que no tienen nada que ver con su empleo.

    REACTIVAR NO DEVUELVE LA AUTORIDAD ANTIGUA. Se reactiva el acceso; los roles
    quedan como estaban, y concederlos otra vez es una decisión que alguien tiene
    que tomar explícitamente. Devolverlos por sorpresa daría permisos que nadie
    acaba de revisar.
    """

    permission_classes = [permissions.IsAuthenticated, HasCompanyMembership]
    throttle_classes = [StaffInviteThrottle]

    def patch(self, request, pk):
        from .models import Membership

        company = _company_or_none(
            request, request.data.get('company') or request.query_params.get('company'),
        )
        if not company:
            return _deny_not_found()
        if not has_capability(request.user, company, CAP_STAFF_MANAGE):
            return _deny_authority()

        membership = Membership.objects.filter(company=company, pk=pk).first()
        if membership is None:
            return _deny_not_found()

        if membership.user_id == request.user.id:
            # Quitarse el acceso a uno mismo deja a la empresa potencialmente sin
            # nadie que pueda devolvérselo.
            return Response(
                {'detail': 'No puedes desactivar tu propio acceso.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        activo = request.data.get('is_active')
        if not isinstance(activo, bool):
            return Response(
                {'detail': 'Indique `is_active` como verdadero o falso.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership.is_active = activo
        membership.save(update_fields=['is_active', 'updated_at'])

        AdminAuditLog.log(
            actor=request.user,
            action='membership_reactivated' if activo else 'membership_deactivated',
            target_type='membership', target_id=membership.pk,
            metadata={'membership_id': membership.pk, 'company_id': company.pk,
                      'user_id': membership.user_id},
            request=request, company=company,
        )
        return Response(AdminStaffListView._person(
            Membership.objects.select_related('user', 'branch')
            .prefetch_related('role_assignments__role', 'role_assignments__area',
                              'branch_access__branch')
            .get(pk=membership.pk)
        ))
