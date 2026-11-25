import django.core.validators
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vault", "0004_monotonic_clock_and_profiles"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("host", models.CharField(blank=True, default="", max_length=255)),
                ("port", models.PositiveIntegerField(default=587, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(65535)])),
                ("security", models.CharField(choices=[("starttls", "STARTTLS"), ("ssl", "SSL/TLS"), ("none", "Unencrypted")], default="starttls", max_length=16)),
                ("username", models.CharField(blank=True, default="", max_length=255)),
                ("encrypted_password", models.BinaryField(blank=True, null=True)),
                ("from_email", models.EmailField(blank=True, default="", max_length=254)),
                ("from_name", models.CharField(blank=True, default="", max_length=100)),
                ("revision", models.PositiveBigIntegerField(default=1)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="email_settings", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="AccountabilityPartner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("email", models.EmailField(max_length=254)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="accountability_partners", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["name", "id"],
                "constraints": [models.UniqueConstraint(fields=("owner", "email"), name="unique_partner_email_per_owner")],
            },
        ),
        migrations.CreateModel(
            name="MailDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_id", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("delivery_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("event_type", models.CharField(choices=[("unlock_requested", "Unlock requested"), ("partner_added", "Partner added"), ("partner_removed", "Partner removed"), ("partner_address_changed", "Partner address changed"), ("notifications_disabled", "Notifications disabled"), ("smtp_configuration_changed", "SMTP configuration changed"), ("test", "Test email")], max_length=40)),
                ("recipient_name", models.CharField(blank=True, default="", max_length=100)),
                ("recipient_email", models.EmailField(max_length=254)),
                ("encrypted_payload", models.BinaryField()),
                ("encrypted_transport_snapshot", models.BinaryField(blank=True, null=True)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sending", "Sending"), ("accepted", "Accepted by mail server")], default="pending", max_length=16)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField()),
                ("lease_token", models.UUIDField(blank=True, editable=False, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("needs_attention", models.BooleanField(default=False)),
                ("last_error", models.CharField(blank=True, default="", max_length=240)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mail_deliveries", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [models.Index(fields=["status", "next_attempt_at"], name="mail_delivery_due_idx")],
                "constraints": [models.UniqueConstraint(fields=("event_id", "recipient_email"), name="unique_recipient_per_mail_event")],
            },
        ),
    ]
