"""
Alta de personal por invitación.

INVITAR NO ES DAR ACCESO
------------------------
Crear una invitación no crea `Membership`. Mientras nadie acepte, la persona no
puede entrar. Eso permite revocar sin dejar rastro de acceso, e impide que un
correo escrito con un dedo torcido conceda entrada a un desconocido.

LO QUE NO SE PUEDE AVERIGUAR DESDE AQUÍ
---------------------------------------
Un administrador de la empresa A no puede usar el alta para saber si un correo
está registrado en la plataforma, ni en qué otras empresas trabaja esa persona,
ni qué autoridad tiene allí. La respuesta es la misma exista o no la cuenta.

No es una precaución teórica: un formulario que responde distinto según si el
correo existe es un oráculo de enumeración de cuentas, y en una plataforma
multiempresa además filtra la plantilla de la competencia.

LA ÚNICA EXCEPCIÓN es cuando la persona YA pertenece a ESTA empresa. Eso el
administrador puede saberlo —es su propio personal— y decírselo evita que cree
una invitación que nunca serviría.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Branch, CompanyArea, CompanyRole, Membership, MembershipBranchAccess,
    MembershipRoleAssignment, StaffInvitation, hash_token, make_raw_token,
)

User = get_user_model()


class StaffError(Exception):
    """Una regla de negocio incumplida. Las vistas la traducen a 400."""


class StaffConflict(StaffError):
    """La persona ya forma parte de la empresa. Es información suya, no ajena."""


def normalize_email(value: str) -> str:
    return (value or '').strip().lower()


def _validate_organisation(company, *, role_id, area_id, branch_ids,
                           branch_access_mode):
    """
    Comprueba que rol, área y sucursales son DE ESTA EMPRESA y están activos.

    Se valida contra la empresa y no sólo contra la existencia: un `role_id` de
    otro inquilino no es un error de tipo, es un intento de conceder autoridad
    ajena, y tiene que responder igual que un id inexistente.
    """
    role = CompanyRole.objects.filter(
        company=company, pk=role_id, is_active=True,
    ).first()
    if role is None:
        raise StaffError('El rol indicado no existe en esta empresa.')

    area = None
    if area_id is not None:
        area = CompanyArea.objects.filter(
            company=company, pk=area_id, is_active=True,
        ).first()
        if area is None:
            # Un área desactivada no se ofrece para asignaciones nuevas. El
            # historial de quien ya la tenía se conserva; lo que no se hace es
            # incorporar gente a un área que la empresa retiró.
            raise StaffError('El área indicada no existe o está desactivada.')

    branches: list[int] = []
    if branch_access_mode == Membership.ACCESS_MODE_SELECTED:
        wanted = [int(b) for b in (branch_ids or [])]
        if not wanted:
            raise StaffError(
                'Con alcance por sucursales seleccionadas hay que elegir al '
                'menos una.'
            )
        found = set(
            Branch.objects.filter(company=company, pk__in=wanted, is_active=True)
            .values_list('pk', flat=True)
        )
        missing = [b for b in wanted if b not in found]
        if missing:
            raise StaffError('Alguna sucursal indicada no existe en esta empresa.')
        branches = wanted

    return role, area, branches


def _existing_membership(company, email: str):
    """La membresía de este correo en ESTA empresa, activa o no."""
    return (
        Membership.objects.filter(company=company, user__email__iexact=email)
        .select_related('user').first()
    )


def create_invitation(*, company, email: str, first_name: str, last_name: str,
                      role_id: int, area_id=None,
                      branch_access_mode: str = Membership.ACCESS_MODE_ALL,
                      branch_ids=None, invited_by=None,
                      ttl_days: int = StaffInvitation.DEFAULT_TTL_DAYS,
                      ) -> tuple[StaffInvitation, str, bool]:
    """
    Crea —o reemplaza— la invitación pendiente de este correo en esta empresa.

    Devuelve `(invitación, token_en_claro, es_nueva)`. El token en claro sale de
    aquí UNA vez, para enviarlo por correo; no se guarda en ninguna parte.

    IDEMPOTENTE POR DISEÑO. Un doble clic en «Enviar invitación» no deja dos
    tokens vivos: si ya hay una pendiente para el mismo correo y la misma
    empresa, se REEMPLAZA su token y se actualizan los datos previstos. El token
    anterior deja de servir, que es lo correcto — dos enlaces válidos para la
    misma persona son dos formas de entrar y sólo una se puede revocar a la vez.
    """
    email = normalize_email(email)
    if not email:
        raise StaffError('Hace falta un correo de acceso.')

    role, area, branches = _validate_organisation(
        company, role_id=role_id, area_id=area_id, branch_ids=branch_ids,
        branch_access_mode=branch_access_mode,
    )

    existing = _existing_membership(company, email)
    if existing is not None and existing.is_active:
        # SÍ se dice, y sólo en este caso: es personal propio de quien pregunta.
        raise StaffConflict('Esta persona ya forma parte de la empresa.')

    raw = make_raw_token()
    expires = timezone.now() + timezone.timedelta(days=ttl_days)

    with transaction.atomic():
        pending = StaffInvitation.objects.select_for_update().filter(
            company=company, email=email, status=StaffInvitation.STATUS_PENDING,
        ).first()

        if pending is not None:
            pending.first_name = first_name
            pending.last_name = last_name
            pending.role = role
            pending.area = area
            pending.branch_access_mode = branch_access_mode
            pending.branch_ids = branches
            pending.token_hash = hash_token(raw)
            pending.expires_at = expires
            pending.invited_by = invited_by
            pending.save()
            return pending, raw, False

        try:
            invitation = StaffInvitation.objects.create(
                company=company, email=email,
                first_name=first_name, last_name=last_name,
                role=role, area=area,
                branch_access_mode=branch_access_mode, branch_ids=branches,
                invited_by=invited_by,
                token_hash=hash_token(raw), expires_at=expires,
            )
        except IntegrityError:
            # Otra petición ganó la carrera entre el `select_for_update` y el
            # `create`. La restricción única hizo su trabajo; se reintenta la
            # lectura en vez de devolver un error que el usuario no entendería.
            raise StaffError(
                'Ya hay una invitación en curso para este correo. '
                'Vuelva a intentarlo.'
            ) from None
    return invitation, raw, True


def revoke_invitation(invitation: StaffInvitation) -> StaffInvitation:
    """
    Anula la invitación. El token deja de servir inmediatamente.

    No se borra la fila: quién invitó a quién y cuándo es historial, y borrarlo
    dejaría sin explicación un acceso que existió.
    """
    if invitation.status != StaffInvitation.STATUS_PENDING:
        raise StaffError('Sólo se puede revocar una invitación pendiente.')
    invitation.status = StaffInvitation.STATUS_REVOKED
    # El hash se vacía para que ni siquiera una colisión improbable pueda
    # resucitar el enlace.
    invitation.token_hash = ''
    invitation.save(update_fields=['status', 'token_hash', 'updated_at'])
    return invitation


def resend_invitation(invitation: StaffInvitation, *, ttl_days=None
                      ) -> tuple[StaffInvitation, str]:
    """
    Emite un token NUEVO para una invitación pendiente y reinicia el plazo.

    El anterior deja de servir. Reenviar el mismo token alargaría la vida de un
    enlace que quizá lleva días en un buzón que ya no controla nadie.
    """
    if invitation.status != StaffInvitation.STATUS_PENDING:
        raise StaffError('Sólo se puede reenviar una invitación pendiente.')

    raw = make_raw_token()
    invitation.token_hash = hash_token(raw)
    invitation.expires_at = timezone.now() + timezone.timedelta(
        days=ttl_days or StaffInvitation.DEFAULT_TTL_DAYS,
    )
    invitation.save(update_fields=['token_hash', 'expires_at', 'updated_at'])
    return invitation, raw


def find_invitation(raw_token: str) -> StaffInvitation | None:
    """
    La invitación de este token, si sirve.

    Devuelve `None` para token inexistente, alterado, caducado, revocado o ya
    usado — LA MISMA respuesta para todos. Distinguirlos diría a quien prueba
    tokens si acertó el formato o sólo el plazo.
    """
    if not raw_token:
        return None
    invitation = (
        StaffInvitation.objects
        .select_related('company', 'role', 'area')
        .filter(token_hash=hash_token(raw_token)).first()
    )
    if invitation is None or not invitation.is_usable:
        return None
    return invitation


@transaction.atomic
def accept_invitation(invitation: StaffInvitation, user) -> Membership:
    """
    Convierte la invitación en acceso real. Todo o nada.

    Se vuelve a validar la organización AHORA: entre invitar y aceptar puede
    haber pasado una semana, y el rol pudo desactivarse. Conceder lo que la
    empresa ya retiró sería peor que fallar.

    `select_for_update` sobre la invitación: dos aceptaciones simultáneas del
    mismo enlace deben producir UNA membresía, y la segunda encontrar el token
    ya consumido.
    """
    locked = StaffInvitation.objects.select_for_update().get(pk=invitation.pk)
    if not locked.is_usable:
        raise StaffError('Esta invitación ya no es válida.')

    role, area, branches = _validate_organisation(
        locked.company, role_id=locked.role_id, area_id=locked.area_id,
        branch_ids=locked.branch_ids,
        branch_access_mode=locked.branch_access_mode,
    )

    membership = _existing_membership(locked.company, locked.email)
    if membership is None:
        membership = Membership.objects.create(
            user=user, company=locked.company,
            # EL ROL HEREDADO SE QUEDA EN `customer`, y es deliberado. La
            # autoridad de esta persona son sus `MembershipRoleAssignment`; darle
            # además un rol legacy ampliaría una vía de autorización que esta
            # fase no debe extender.
            role='customer',
            branch_access_mode=locked.branch_access_mode,
            is_active=True,
        )
    else:
        # REACTIVACIÓN: no se crea una segunda membresía. Se reutiliza la fila,
        # que es lo que conserva el historial de ventas, reparaciones y
        # auditoría asociado a esa persona en esta empresa.
        membership.user = user
        membership.is_active = True
        membership.branch_access_mode = locked.branch_access_mode
        membership.save(update_fields=[
            'user', 'is_active', 'branch_access_mode', 'updated_at',
        ])

    MembershipRoleAssignment.objects.get_or_create(
        membership=membership, role=role, area=area,
        defaults={'is_active': True, 'assigned_by': locked.invited_by},
    )

    if locked.branch_access_mode == Membership.ACCESS_MODE_SELECTED:
        # Se reemplaza el alcance, no se acumula: la invitación dice cuáles son
        # las sucursales de esta persona, y sumar a las de una etapa anterior le
        # daría acceso que nadie acaba de conceder.
        MembershipBranchAccess.objects.filter(membership=membership).update(
            is_active=False,
        )
        for branch_id in branches:
            access, _ = MembershipBranchAccess.objects.get_or_create(
                membership=membership, branch_id=branch_id,
                defaults={'granted_by': locked.invited_by},
            )
            if not access.is_active:
                access.is_active = True
                access.save(update_fields=['is_active', 'updated_at'])

    locked.status = StaffInvitation.STATUS_ACCEPTED
    locked.accepted_at = timezone.now()
    locked.membership = membership
    # El token se consume: un enlace usado no vuelve a funcionar.
    locked.token_hash = ''
    locked.save(update_fields=[
        'status', 'accepted_at', 'membership', 'token_hash', 'updated_at',
    ])
    return membership
