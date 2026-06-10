from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from backend.agents.permissions import IsReferralAgent
from backend.agents.serializers import AgentDepositSerializer, ReferralAgentSerializer
from backend.agents.services.dashboard import build_dashboard, deposits_queryset_for_agent


class AgentDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsReferralAgent]

    def get(self, request):
        agent = request.user.referral_agent
        return Response(build_dashboard(agent))


class AgentDepositsView(APIView):
    permission_classes = [IsAuthenticated, IsReferralAgent]

    def get(self, request):
        agent = request.user.referral_agent
        qs = deposits_queryset_for_agent(agent).order_by("-transaction__created_at")[:200]
        rows = []
        for deposit in qs:
            tx = deposit.transaction
            account = tx.account
            user = account.user
            rows.append(
                {
                    "id": deposit.id,
                    "reference": tx.reference,
                    "status": tx.status,
                    "amount": str(tx.amount),
                    "net_amount": str(tx.net_amount),
                    "currency": account.currency,
                    "member_phone": user.phone,
                    "member_name": f"{user.first_name or ''} {user.last_name or ''}".strip() or user.phone,
                    "provider": deposit.provider or tx.method,
                    "created_at": tx.created_at,
                }
            )
        return Response(AgentDepositSerializer(rows, many=True).data)


class AgentProfileView(APIView):
    permission_classes = [IsAuthenticated, IsReferralAgent]

    def get(self, request):
        return Response(ReferralAgentSerializer(request.user.referral_agent).data)

    def patch(self, request):
        agent = request.user.referral_agent
        hide_totals = request.data.get("hide_totals")
        if hide_totals is not None:
            agent.hide_totals = bool(hide_totals)
            agent.save(update_fields=["hide_totals"])
        return Response(ReferralAgentSerializer(agent).data)


class AgentMeView(APIView):
    """Check if the logged-in user is a referral agent (for frontend routing)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        agent = getattr(request.user, "referral_agent", None)
        if not agent or not agent.is_active:
            return Response({"is_agent": False})
        return Response(
            {
                "is_agent": True,
                "agent": ReferralAgentSerializer(agent).data,
            }
        )
