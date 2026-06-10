from django.urls import path

from backend.accounts import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("me/", views.MeView.as_view(), name="me"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password-reset"),
    path("account/summary/", views.AccountSummaryView.as_view(), name="account-summary"),
    path("account/transactions/", views.AccountTransactionsView.as_view(), name="account-transactions"),
    path("account/deposits/", views.AccountDepositsView.as_view(), name="account-deposits"),
    path("account/withdrawals/", views.AccountWithdrawalsView.as_view(), name="account-withdrawals"),
    path("account/bets/", views.AccountBetsView.as_view(), name="account-bets"),
    path("booking/<str:code>/", views.BookingCodeView.as_view(), name="booking-code"),
    path("account/payout-settings/", views.AccountPayoutSettingsView.as_view(), name="account-payout-settings"),
]
