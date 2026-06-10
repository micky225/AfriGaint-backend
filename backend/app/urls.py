from django.urls import path

from backend.app import views

urlpatterns = [
    path("odds/upcoming", views.UpcomingMatchesView.as_view(), name="odds-upcoming"),
    path("odds/live", views.LiveMatchesView.as_view(), name="odds-live"),
    path("odds/match/<str:event_id>", views.MatchDetailView.as_view(), name="odds-match-detail"),
]
