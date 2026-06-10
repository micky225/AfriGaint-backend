from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from backend.agents.models import AgentType, ReferralAgent

User = get_user_model()


@receiver(post_save, sender=ReferralAgent)
def sync_agent_user_permissions(sender, instance: ReferralAgent, **kwargs):
    """Verified referral agents can log in and open /admin."""
    if not instance.is_active:
        return
    user = instance.user
    updates = []
    if not user.is_staff:
        user.is_staff = True
        updates.append("is_staff")
    if instance.agent_type == AgentType.SUPER and not user.is_superuser:
        user.is_superuser = True
        updates.append("is_superuser")
    if updates:
        user.save(update_fields=updates)
