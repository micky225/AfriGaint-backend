import random

from django.conf import settings
from django.db import models


class AgentType(models.TextChoices):
    SUPER = "super", "Super Admin"
    AGENT = "agent", "Agent"


class ReferralAgent(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="referral_agent",
    )
    referral_code = models.CharField(max_length=20, unique=True, db_index=True)
    agent_type = models.CharField(
        max_length=20,
        choices=AgentType.choices,
        default=AgentType.AGENT,
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    display_name = models.CharField(max_length=120, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    hide_totals = models.BooleanField(
        default=False,
        help_text="When enabled, dashboard shows zero totals instead of real figures.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        label = self.display_name or self.user.phone
        return f"{label} ({self.referral_code})"

    @classmethod
    def generate_code(cls) -> str:
        for _ in range(50):
            code = str(random.randint(1000, 999999))
            if not cls.objects.filter(referral_code=code).exists():
                return code
        raise RuntimeError("Could not generate a unique referral code.")

    def save(self, *args, **kwargs):
        if not self.referral_code:
            self.referral_code = self.generate_code()
        if not self.phone:
            self.phone = self.user.phone
        super().save(*args, **kwargs)
