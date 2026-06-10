from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_betleg_selection_key_betleg_settled_at_betleg_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="myaccount",
            name="withdrawal_deposit_count",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Number of qualifying deposits completed toward withdrawal unlock (0-3).",
            ),
        ),
        migrations.AlterField(
            model_name="myaccount",
            name="current_balance",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Display balance shown to the user (dummy credits from deposits).",
                max_digits=14,
            ),
        ),
    ]
