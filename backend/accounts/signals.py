from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from backend.accounts.models import BetStatus, BetTicket
from backend.accounts.services.betting import apply_bet_settlement


@receiver(pre_save, sender=BetTicket)
def store_previous_bet_status(sender, instance, **kwargs):
    if instance.pk:
        previous = BetTicket.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        instance._previous_status = previous
    else:
        instance._previous_status = None


@receiver(post_save, sender=BetTicket)
def settle_bet_on_status_change(sender, instance, created, **kwargs):
    if created:
        return

    previous_status = getattr(instance, "_previous_status", None)
    if previous_status == BetStatus.OPEN and instance.status != BetStatus.OPEN:
        apply_bet_settlement(instance)
