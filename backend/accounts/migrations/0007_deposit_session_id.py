from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_referral_agents"),
    ]

    operations = [
        migrations.AddField(
            model_name="deposit",
            name="session_id",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
