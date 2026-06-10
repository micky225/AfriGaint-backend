from rest_framework import serializers

from backend.agents.models import ReferralAgent


class ReferralAgentSerializer(serializers.ModelSerializer):
    user_phone = serializers.CharField(source="user.phone", read_only=True)

    class Meta:
        model = ReferralAgent
        fields = [
            "id",
            "referral_code",
            "agent_type",
            "display_name",
            "phone",
            "hide_totals",
            "is_active",
            "user_phone",
            "created_at",
        ]


class AgentDepositSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reference = serializers.CharField()
    status = serializers.CharField()
    amount = serializers.CharField()
    net_amount = serializers.CharField()
    currency = serializers.CharField()
    member_phone = serializers.CharField()
    member_name = serializers.CharField()
    provider = serializers.CharField()
    created_at = serializers.DateTimeField()
