from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from backend.agents.admin import ReferralAgentInline

from backend.accounts.models import (
    AccountTransaction,
    BetHistory,
    BetLeg,
    BetTicket,
    Deposit,
    MyAccount,
    PayoutSetting,
    PhoneOtp,
    User,
    Withdrawal,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["phone"]
    list_display = [
        "phone",
        "email",
        "first_name",
        "last_name",
        "is_phone_verified",
        "is_staff",
        "referral_agent_code",
    ]
    search_fields = ["phone", "email", "first_name", "last_name"]
    inlines = [ReferralAgentInline]
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "is_phone_verified")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("phone", "password1", "password2")}),
    )

    @admin.display(description="Referral code")
    def referral_agent_code(self, obj: User):
        agent = getattr(obj, "referral_agent", None)
        return agent.referral_code if agent else "—"


@admin.register(PhoneOtp)
class PhoneOtpAdmin(admin.ModelAdmin):
    list_display = ["phone", "purpose", "created_at", "expires_at", "attempts", "is_used"]
    list_filter = ["purpose", "is_used"]


@admin.register(MyAccount)
class MyAccountAdmin(admin.ModelAdmin):
    raw_id_fields = ["referred_by_agent"]
    list_display = [
        "user",
        "referred_by_agent",
        "referral_code_used",
        "currency",
        "current_balance",
        "locked_balance",
        "withdrawal_deposit_count",
        "updated_at",
    ]
    search_fields = ["user__phone", "user__email"]


@admin.register(AccountTransaction)
class AccountTransactionAdmin(admin.ModelAdmin):
    list_display = ["reference", "account", "tx_type", "status", "method", "amount", "created_at"]
    list_filter = ["tx_type", "status", "method"]
    search_fields = ["reference", "account__user__phone", "provider_reference"]


@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    list_display = ["transaction", "provider", "phone_number"]
    search_fields = ["transaction__reference", "phone_number", "provider"]


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = ["transaction", "account_name", "account_number", "provider"]
    search_fields = ["transaction__reference", "account_name", "account_number"]


@admin.register(PayoutSetting)
class PayoutSettingAdmin(admin.ModelAdmin):
    list_display = ["account", "payout_type", "account_name", "account_number", "is_default", "is_active"]
    list_filter = ["payout_type", "is_default", "is_active"]
    search_fields = ["account__user__phone", "account_name", "account_number", "provider_name"]


@admin.register(BetHistory)
class BetHistoryAdmin(admin.ModelAdmin):
    list_display = ["id", "account", "match_label", "stake", "odd", "possible_win", "status", "placed_at"]
    list_filter = ["status"]
    search_fields = ["account__user__phone", "match_label", "market_label", "selection_label"]


class BetLegInline(admin.TabularInline):
    model = BetLeg
    extra = 0
    readonly_fields = ["match_id", "match_label", "market_label", "selection_label", "odd"]


@admin.register(BetTicket)
class BetTicketAdmin(admin.ModelAdmin):
    list_display = [
        "booking_code",
        "account",
        "stake",
        "total_odds",
        "possible_win",
        "payout",
        "status",
        "placed_at",
        "settled_at",
    ]
    list_filter = ["status"]
    search_fields = ["booking_code", "account__user__phone"]
    inlines = [BetLegInline]
    readonly_fields = ["booking_code", "placed_at", "settled_at"]
    actions = ["mark_as_won", "mark_as_lost", "mark_as_void"]

    @admin.action(description="Mark selected bets as WON")
    def mark_as_won(self, request, queryset):
        for ticket in queryset.filter(status="open"):
            ticket.status = "won"
            ticket.save(update_fields=["status"])

    @admin.action(description="Mark selected bets as LOST")
    def mark_as_lost(self, request, queryset):
        for ticket in queryset.filter(status="open"):
            ticket.status = "lost"
            ticket.save(update_fields=["status"])

    @admin.action(description="Mark selected bets as VOID (refund stake)")
    def mark_as_void(self, request, queryset):
        for ticket in queryset.filter(status="open"):
            ticket.status = "void"
            ticket.save(update_fields=["status"])


@admin.register(BetLeg)
class BetLegAdmin(admin.ModelAdmin):
    list_display = ["ticket", "match_label", "selection_label", "odd"]
    search_fields = ["ticket__booking_code", "match_label", "selection_label"]
