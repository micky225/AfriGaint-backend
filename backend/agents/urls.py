from django.urls import path

from backend.agents import views

urlpatterns = [
    path("me/", views.AgentMeView.as_view(), name="agent-me"),
    path("dashboard/", views.AgentDashboardView.as_view(), name="agent-dashboard"),
    path("deposits/", views.AgentDepositsView.as_view(), name="agent-deposits"),
    path("profile/", views.AgentProfileView.as_view(), name="agent-profile"),
]
