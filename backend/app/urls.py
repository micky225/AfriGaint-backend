from django.urls import path

from backend.app import views

urlpatterns = [
    path("odds/upcoming", views.UpcomingMatchesView.as_view(), name="odds-upcoming"),
    path("odds/live", views.LiveMatchesView.as_view(), name="odds-live"),
    path("odds/leagues", views.LeagueListView.as_view(), name="odds-leagues"),
    path("odds/top-matches", views.TopMatchesView.as_view(), name="odds-top-matches"),
    path("odds/search", views.MatchSearchView.as_view(), name="odds-search"),
    path("odds/match/<str:event_id>", views.MatchDetailView.as_view(), name="odds-match-detail"),
]
