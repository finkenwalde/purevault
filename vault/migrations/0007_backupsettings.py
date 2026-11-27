from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("vault", "0006_remove_accountability_email"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_key", models.BinaryField()),
                ("recovery_bundle", models.BinaryField()),
                ("interval_days", models.PositiveSmallIntegerField(default=0)),
                ("retention", models.PositiveSmallIntegerField(default=7)),
                ("next_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=160)),
                ("run_token", models.UUIDField(blank=True, null=True)),
                ("lease_until", models.DateTimeField(blank=True, null=True)),
                ("owner", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
