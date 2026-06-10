from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "backend.agents"
    verbose_name = "Referral Agents"

    def ready(self):
        import backend.agents.signals  # noqa: F401
