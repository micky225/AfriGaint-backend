from django.contrib import admin, messages
from django.utils.html import format_html

from backend.agents.models import AgentType, ReferralAgent
from backend.agents.utils import member_registration_url


@admin.register(ReferralAgent)
class ReferralAgentAdmin(admin.ModelAdmin):
    list_display = [
        "referral_code",
        "display_name",
        "user",
        "agent_type",
        "phone",
        "is_active",
        "referral_link_short",
        "created_at",
    ]
    list_filter = ["agent_type", "is_active", "hide_totals"]
    search_fields = ["referral_code", "display_name", "user__phone", "phone"]
    autocomplete_fields = ["user", "parent"]
    readonly_fields = ["referral_code", "referral_link_display", "created_at"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "user",
                    "agent_type",
                    "display_name",
                    "phone",
                    "parent",
                    "is_active",
                    "hide_totals",
                )
            },
        ),
        (
            "Referral link (auto-generated on save)",
            {
                "fields": ("referral_code", "referral_link_display", "created_at"),
            },
        ),
    )

    @admin.display(description="Referral link")
    def referral_link_short(self, obj: ReferralAgent):
        if not obj.referral_code:
            return "—"
        return member_registration_url(obj.referral_code)

    @admin.display(description="Member registration link")
    def referral_link_display(self, obj: ReferralAgent):
        if not obj.pk or not obj.referral_code:
            return "Save this agent first — a referral code and link will be generated automatically."
        link = member_registration_url(obj.referral_code)
        return format_html(
            '<p style="margin:0 0 8px;"><strong>{}</strong></p>'
            '<p style="margin:0;color:#555;">Member registration page — share with new players. '
            "They sign up on /register and are linked to this agent. "
            "The agent opens their dashboard via the <strong>Admin</strong> button after login.</p>",
            link,
        )

    @admin.action(description="Verify selected agents (activate + grant staff login)")
    def verify_agents(self, request, queryset):
        updated = queryset.update(is_active=True)
        for agent in queryset.select_related("user"):
            if not agent.user.is_staff:
                agent.user.is_staff = True
                agent.user.save(update_fields=["is_staff"])
        self.message_user(
            request,
            f"{updated} agent(s) verified. They can log in and open /admin to see their link.",
            messages.SUCCESS,
        )

    actions = ["verify_agents"]


class ReferralAgentInline(admin.StackedInline):
    model = ReferralAgent
    can_delete = True
    extra = 0
    max_num = 1
    readonly_fields = ["referral_code", "referral_link_display", "created_at"]
    fields = [
        "agent_type",
        "display_name",
        "is_active",
        "referral_code",
        "referral_link_display",
        "created_at",
    ]

    @admin.display(description="Member registration link")
    def referral_link_display(self, obj: ReferralAgent):
        if not obj.pk or not obj.referral_code:
            return "Save the user — referral code and link are created automatically."
        return member_registration_url(obj.referral_code)
