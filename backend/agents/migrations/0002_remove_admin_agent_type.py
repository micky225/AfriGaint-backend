from django.db import migrations, models


def migrate_admin_to_agent(apps, schema_editor):
    ReferralAgent = apps.get_model("agents", "ReferralAgent")
    ReferralAgent.objects.filter(agent_type="admin").update(agent_type="agent")


class Migration(migrations.Migration):
    dependencies = [
        ("agents", "0001_referral_agents"),
    ]

    operations = [
        migrations.RunPython(migrate_admin_to_agent, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="referralagent",
            name="agent_type",
            field=models.CharField(
                choices=[("super", "Super Admin"), ("agent", "Agent")],
                default="agent",
                max_length=20,
            ),
        ),
    ]
