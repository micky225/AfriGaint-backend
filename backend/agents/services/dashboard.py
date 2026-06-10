from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from backend.accounts.models import Deposit, MyAccount, TransactionStatus, TransactionType
from backend.agents.models import AgentType, ReferralAgent
from backend.agents.utils import member_registration_url

REFERRER_SHARE_RATE = Decimal("0.5")
PLATFORM_SHARE_RATE = Decimal("0.5")


def _zero_bucket() -> dict:
    return {
        "approved_count": 0,
        "approved_amount": "0.00",
        "platform_share": "0.00",
        "referrer_share": "0.00",
    }


def _period_starts(now):
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    return {
        "today": today_start,
        "yesterday": yesterday_start,
        "week": week_start,
        "month": month_start,
    }


def deposits_queryset_for_agent(agent: ReferralAgent):
    base = Deposit.objects.select_related("transaction", "transaction__account", "transaction__account__user")
    if agent.agent_type == AgentType.SUPER:
        return base.filter(transaction__tx_type=TransactionType.DEPOSIT)
    return base.filter(transaction__account__referred_by_agent=agent)


def _aggregate_deposits(qs, *, start=None, end=None, status=TransactionStatus.COMPLETED):
    filtered = qs.filter(transaction__status=status)
    if start is not None:
        filtered = filtered.filter(transaction__created_at__gte=start)
    if end is not None:
        filtered = filtered.filter(transaction__created_at__lt=end)

    totals = filtered.aggregate(
        count=Count("id"),
        amount=Sum("transaction__amount"),
    )
    count = totals["count"] or 0
    amount = Decimal(totals["amount"] or 0).quantize(Decimal("0.01"))
    referrer_share = (amount * REFERRER_SHARE_RATE).quantize(Decimal("0.01"))
    platform_share = (amount * PLATFORM_SHARE_RATE).quantize(Decimal("0.01"))
    return {
        "approved_count": count,
        "approved_amount": str(amount),
        "platform_share": str(platform_share),
        "referrer_share": str(referrer_share),
    }


def _agent_deposit_stats(agent: ReferralAgent, *, hide_totals: bool = False) -> dict:
    if hide_totals:
        return {
            "approved_count": 0,
            "approved_amount": "0.00",
            "rejected_count": 0,
            "rejected_amount": "0.00",
            "platform_share": "0.00",
            "referrer_share": "0.00",
            "member_count": 0,
        }

    deposits_qs = Deposit.objects.filter(
        transaction__tx_type=TransactionType.DEPOSIT,
        transaction__account__referred_by_agent=agent,
    )
    approved = deposits_qs.filter(transaction__status=TransactionStatus.COMPLETED).aggregate(
        count=Count("id"),
        amount=Sum("transaction__amount"),
    )
    rejected = deposits_qs.filter(transaction__status=TransactionStatus.FAILED).aggregate(
        count=Count("id"),
        amount=Sum("transaction__amount"),
    )
    approved_amount = Decimal(approved["amount"] or 0).quantize(Decimal("0.01"))
    return {
        "approved_count": approved["count"] or 0,
        "approved_amount": str(approved_amount),
        "rejected_count": rejected["count"] or 0,
        "rejected_amount": str(Decimal(rejected["amount"] or 0).quantize(Decimal("0.01"))),
        "platform_share": str((approved_amount * PLATFORM_SHARE_RATE).quantize(Decimal("0.01"))),
        "referrer_share": str((approved_amount * REFERRER_SHARE_RATE).quantize(Decimal("0.01"))),
        "member_count": MyAccount.objects.filter(referred_by_agent=agent).count(),
    }


def build_super_admin_roster(*, hide_totals: bool = False) -> list[dict]:
    agents = (
        ReferralAgent.objects.filter(agent_type=AgentType.AGENT)
        .select_related("user")
        .order_by("-created_at")
    )
    roster = []
    for entry in agents:
        stats = _agent_deposit_stats(entry, hide_totals=hide_totals)
        roster.append(
            {
                "id": entry.id,
                "referral_code": entry.referral_code,
                "display_name": entry.display_name or entry.user.get_full_name() or entry.user.phone,
                "phone": entry.phone or entry.user.phone,
                "is_active": entry.is_active,
                **stats,
            }
        )
    return roster


def build_dashboard(agent: ReferralAgent) -> dict:
    now = timezone.now()
    periods = _period_starts(now)
    deposits_qs = deposits_queryset_for_agent(agent)
    rejected_qs = deposits_queryset_for_agent(agent).filter(transaction__status=TransactionStatus.FAILED)

    if agent.hide_totals:
        breakdown = {key: _zero_bucket() for key in ("today", "yesterday", "week", "month", "all_time")}
        summary = {
            "approved_count": 0,
            "approved_amount": "0.00",
            "platform_share": "0.00",
            "referrer_share": "0.00",
            "rejected_count": 0,
            "rejected_amount": "0.00",
            "member_count": 0,
        }
    else:
        breakdown = {
            "today": _aggregate_deposits(deposits_qs, start=periods["today"]),
            "yesterday": _aggregate_deposits(
                deposits_qs,
                start=periods["yesterday"],
                end=periods["today"],
            ),
            "week": _aggregate_deposits(deposits_qs, start=periods["week"]),
            "month": _aggregate_deposits(deposits_qs, start=periods["month"]),
            "all_time": _aggregate_deposits(deposits_qs),
        }
        rejected = rejected_qs.aggregate(
            count=Count("id"),
            amount=Sum("transaction__amount"),
        )
        if agent.agent_type == AgentType.SUPER:
            member_count = MyAccount.objects.filter(referred_by_agent__isnull=False).count()
        else:
            member_count = MyAccount.objects.filter(referred_by_agent=agent).count()

        summary = {
            **breakdown["all_time"],
            "rejected_count": rejected["count"] or 0,
            "rejected_amount": str(Decimal(rejected["amount"] or 0).quantize(Decimal("0.01"))),
            "member_count": member_count,
        }

    agent_payload = {
        "id": agent.id,
        "referral_code": agent.referral_code,
        "agent_type": agent.agent_type,
        "display_name": agent.display_name or agent.user.get_full_name() or agent.user.phone,
        "phone": agent.phone or agent.user.phone,
        "hide_totals": agent.hide_totals,
    }
    if agent.agent_type == AgentType.AGENT:
        agent_payload["registration_link"] = member_registration_url(agent.referral_code)

    result = {
        "agent": agent_payload,
        "share_rate": str(REFERRER_SHARE_RATE),
        "summary": summary,
        "breakdown": breakdown,
    }

    if agent.agent_type == AgentType.SUPER:
        result["agents_roster"] = build_super_admin_roster(hide_totals=agent.hide_totals)

    return result
