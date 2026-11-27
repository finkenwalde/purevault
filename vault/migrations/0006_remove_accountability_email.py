from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("vault", "0005_emailsettings_accountabilitypartner_maildelivery"),
    ]

    operations = [
        migrations.DeleteModel(name="MailDelivery"),
        migrations.DeleteModel(name="EmailSettings"),
        migrations.DeleteModel(name="AccountabilityPartner"),
        # These records cannot be recovered by recreating empty tables.
        migrations.RunPython(migrations.RunPython.noop),
    ]
