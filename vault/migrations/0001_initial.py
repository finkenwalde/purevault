import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [("auth", "0012_alter_user_first_name_max_length")]
    operations = [
        migrations.CreateModel(
            name="VaultEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("encrypted_secret", models.BinaryField(editable=False)),
                ("unlock_delay_seconds", models.PositiveIntegerField()),
                ("relock_policy", models.CharField(choices=[("automatic", "Automatic"), ("manual", "Manual")], default="automatic", max_length=16)),
                ("relock_after_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="vault_entries", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["title", "id"]},
        ),
        migrations.CreateModel(
            name="UnlockGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("requested_at", models.DateTimeField()),
                ("eligible_at", models.DateTimeField()),
                ("access_until", models.DateTimeField(blank=True, null=True)),
                ("release_count", models.PositiveIntegerField(default=0)),
                ("last_released_at", models.DateTimeField(blank=True, null=True)),
                ("entry", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="unlock_grant", to="vault.vaultentry")),
            ],
        ),
        migrations.AddConstraint(model_name="vaultentry", constraint=models.CheckConstraint(condition=(models.Q(("relock_policy", "manual"), ("relock_after_seconds__isnull", True)) | models.Q(("relock_policy", "automatic"), ("relock_after_seconds__isnull", False))), name="valid_relock_configuration")),
    ]

