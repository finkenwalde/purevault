import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def initialize_clock_and_invalidate_legacy_grants(apps, schema_editor):
    RuntimeClock = apps.get_model("vault", "RuntimeClock")
    UnlockGrant = apps.get_model("vault", "UnlockGrant")
    RuntimeClock.objects.create(id=1, generation="", elapsed_milliseconds=0, checkpoint_monotonic_ns=0)
    # Legacy grants used wall-clock deadlines. Clearing them can only lengthen access.
    UnlockGrant.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("vault", "0003_installation_state"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="RuntimeClock",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("generation", models.CharField(blank=True, default="", max_length=36)),
                ("elapsed_milliseconds", models.PositiveBigIntegerField(default=0)),
                ("checkpoint_monotonic_ns", models.PositiveBigIntegerField(default=0)),
            ],
            options={"constraints": [models.CheckConstraint(condition=models.Q(("id", 1)), name="single_runtime_clock")]},
        ),
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("display_timezone", models.CharField(blank=True, default="", max_length=128)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="purevault_profile", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(model_name="unlockgrant", name="requested_runtime_ms", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="unlockgrant", name="eligible_runtime_ms", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.AddField(model_name="unlockgrant", name="access_until_runtime_ms", field=models.PositiveBigIntegerField(blank=True, null=True)),
        migrations.RunPython(initialize_clock_and_invalidate_legacy_grants, migrations.RunPython.noop),
    ]

