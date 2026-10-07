from datetime import timedelta
from django.db import migrations, models

def set_legacy_unlock_expiry(apps, schema_editor):
    Entry = apps.get_model("vault", "Entry")
    for entry in Entry.objects.filter(unlock_at__isnull=False, unlock_expires_at__isnull=True).iterator():
        entry.unlock_expires_at = entry.unlock_at + timedelta(seconds=900)
        entry.save(update_fields=["unlock_expires_at"])

class Migration(migrations.Migration):
    dependencies = [("vault", "0002_entry_unlock_at")]
    operations = [
        migrations.AlterField(
            model_name="entry",
            name="website",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="entry",
            name="unlock_duration_seconds",
            field=models.PositiveBigIntegerField(default=900),
        ),
        migrations.AddField(
            model_name="entry",
            name="unlock_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(set_legacy_unlock_expiry, migrations.RunPython.noop),
    ]
