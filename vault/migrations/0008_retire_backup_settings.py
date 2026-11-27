from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("vault", "0007_backupsettings")]

    operations = [
        # Leave historical settings intact for existing installations.
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="BackupSettings")],
        ),
    ]
