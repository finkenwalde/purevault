from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(name="VaultConfig", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("password_hash", models.CharField(max_length=256)),
            ("key_salt", models.CharField(max_length=64)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ]),
        migrations.CreateModel(name="Entry", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("title", models.CharField(max_length=160)),
            ("username", models.CharField(blank=True, max_length=254)),
            ("website", models.URLField(blank=True)),
            ("notes", models.TextField(blank=True)),
            ("secret_ciphertext", models.TextField()),
            ("delay_seconds", models.PositiveBigIntegerField(default=86400)),
            ("unlock_requested_at", models.DateTimeField(blank=True, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
        ]),
    ]
