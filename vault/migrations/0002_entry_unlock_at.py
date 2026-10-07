from django.db import migrations, models
from datetime import timedelta

def preserve_pending_unlocks(apps, schema_editor):
    Entry = apps.get_model("vault", "Entry")
    for entry in Entry.objects.filter(unlock_requested_at__isnull=False, unlock_at__isnull=True).iterator():
        entry.unlock_at = entry.unlock_requested_at + timedelta(seconds=entry.delay_seconds)
        entry.save(update_fields=["unlock_at"])

class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]
    operations = [migrations.AddField(
        model_name="entry",
        name="unlock_at",
        field=models.DateTimeField(blank=True, null=True),
    ), migrations.RunPython(preserve_pending_unlocks, migrations.RunPython.noop)]
