from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender=get_user_model())
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        from .models import UserProfile
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={'role': UserProfile.ROLE_CUSTOMER},
        )


# AUTH-B — a session becomes staff the moment its account does, not at the next
# renewal: the 8-hour cap applies at once. `harden` only ever shortens.
def _harden_if_internal(user):
    from . import auth_sessions
    if user is not None and auth_sessions.is_internal_account(user):
        auth_sessions.harden(user)


def _register_session_hardening():
    from .models import Membership, UserProfile

    @receiver(post_save, sender=Membership, dispatch_uid='auth_session_harden_membership')
    def harden_on_membership(sender, instance, **kwargs):
        if instance.is_active:
            _harden_if_internal(instance.user)

    @receiver(post_save, sender=UserProfile, dispatch_uid='auth_session_harden_profile')
    def harden_on_profile(sender, instance, **kwargs):
        if instance.role != UserProfile.ROLE_CUSTOMER:
            _harden_if_internal(instance.user)

    @receiver(post_save, sender=get_user_model(), dispatch_uid='auth_session_harden_user')
    def harden_on_user(sender, instance, created, **kwargs):
        if not created and (instance.is_staff or instance.is_superuser):
            _harden_if_internal(instance)


_register_session_hardening()
