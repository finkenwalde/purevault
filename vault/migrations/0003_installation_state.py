from django.db import migrations, models


def seed_installation_state(apps, schema_editor):
    InstallationState = apps.get_model("vault", "InstallationState")
    User = apps.get_model("auth", "User")
    InstallationState.objects.create(id=1, configured=User.objects.exists())


class Migration(migrations.Migration):
    dependencies = [("vault", "0002_align_duration_validators")]
    operations = [
        migrations.CreateModel(
            name="InstallationState",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ("configured", models.BooleanField(default=False)),
            ],
            options={"constraints": [models.CheckConstraint(condition=models.Q(("id", 1)), name="single_installation_state")]},
        ),
        migrations.RunPython(seed_installation_state, migrations.RunPython.noop),
    ]

