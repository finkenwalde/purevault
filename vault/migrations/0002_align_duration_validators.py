import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]
    operations = [
        migrations.RemoveConstraint(model_name="vaultentry", name="valid_relock_configuration"),
        migrations.AlterField(
            model_name="vaultentry",
            name="relock_after_seconds",
            field=models.PositiveIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(2592000)]),
        ),
        migrations.AlterField(
            model_name="vaultentry",
            name="unlock_delay_seconds",
            field=models.PositiveIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(31536000)]),
        ),
        migrations.AddConstraint(
            model_name="vaultentry",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("relock_after_seconds__isnull", True), ("relock_policy", "manual")), models.Q(("relock_after_seconds__isnull", False), ("relock_policy", "automatic")), _connector="OR"), name="valid_relock_configuration"),
        ),
    ]

