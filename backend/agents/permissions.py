from rest_framework.permissions import BasePermission


class IsReferralAgent(BasePermission):
    message = "Referral agent access required."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        agent = getattr(user, "referral_agent", None)
        return agent is not None and agent.is_active
