import json
import os
import tempfile
import smtplib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.db.models.query import QuerySet
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from vault.crypto import decrypt_text, encrypt_secret, encrypt_text
from vault.mail_service import _send_smtp, claim_due_delivery, process_claimed_delivery, queue_delivery
from vault.models import AccountabilityPartner, EmailSettings, InstallationState, MailDelivery, RuntimeClock, UnlockGrant, UserProfile, VaultEntry
from vault.session_activity import SESSION_ACTIVITY_GENERATION_KEY, SESSION_ACTIVITY_RUNTIME_KEY
from vault.services import (
    ClockUnavailable, SetupAlreadyComplete, advance_runtime_clock, cancel_or_relock,
    create_initial_user, grant_status, initialize_runtime_clock, release_secret,
    start_unlock, update_entry_if_available, EntryNotAvailable,
)


BASE_NS = 1_000_000_000_000
GENERATION = "00000000-0000-0000-0000-000000000001"


def force_login_active(client, user, runtime_ms=0, generation=GENERATION):
    client.force_login(user)
    session = client.session
    session[SESSION_ACTIVITY_RUNTIME_KEY] = runtime_ms
    session[SESSION_ACTIVITY_GENERATION_KEY] = generation
    session.save()


class RuntimeClockMixin:
    def initialize_test_clock(self):
        RuntimeClock.objects.update_or_create(pk=1, defaults={
            "generation": GENERATION, "elapsed_milliseconds": 0,
            "checkpoint_monotonic_ns": BASE_NS,
        })
        self.env = patch.dict(os.environ, {"PUREVAULT_CLOCK_GENERATION": GENERATION})
        self.env.start()

    def set_runtime(self, seconds):
        milliseconds = int(seconds * 1000)
        RuntimeClock.objects.filter(pk=1).update(
            elapsed_milliseconds=milliseconds,
            checkpoint_monotonic_ns=BASE_NS + milliseconds * 1_000_000,
        )

    def mono(self, seconds):
        return patch("vault.services.time.monotonic_ns", return_value=BASE_NS + int(seconds * 1_000_000_000))


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class VaultSecurityTests(RuntimeClockMixin, TestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.key_dir = tempfile.TemporaryDirectory()
        self.key_override = override_settings(PUREVAULT_KEY_FILE=Path(self.key_dir.name) / "app.key")
        self.key_override.enable()
        self.user = User.objects.create_user("example-user", password="long-neutral-test-password")
        self.other = User.objects.create_user("other-user", password="another-neutral-test-password")
        self.entry = VaultEntry.objects.create(
            owner=self.user, title="Example account", encrypted_secret=encrypt_secret("<tag>&9 ! exact"),
            unlock_delay_seconds=60, relock_policy="automatic", relock_after_seconds=30,
        )
        self.client = Client(); force_login_active(self.client, self.user)

    def tearDown(self):
        self.key_override.disable(); self.key_dir.cleanup(); self.env.stop()

    def post(self, path, data=None):
        return self.client.post(path, data=json.dumps(data or {}), content_type="application/json")

    def start(self):
        with self.mono(0):
            return start_unlock(self.entry)

    def test_unauthenticated_and_owner_checks(self):
        anonymous = Client()
        self.assertEqual(anonymous.get("/api/entries/").status_code, 401)
        force_login_active(self.client, self.other)
        self.assertEqual(self.post(f"/api/entries/{self.entry.id}/reveal/").status_code, 404)

    def test_early_reveal_and_metadata_do_not_expose_secret(self):
        self.start(); self.set_runtime(59)
        with self.mono(59):
            self.assertEqual(self.post(f"/api/entries/{self.entry.id}/reveal/").status_code, 423)
        content = self.client.get("/api/entries/").content
        self.assertNotIn(b"<tag>", content); self.assertNotIn(b"exact", content)

    def test_exact_unlock_and_expiry_boundaries(self):
        self.start(); self.set_runtime(60)
        with self.mono(60):
            self.assertEqual(release_secret(self.entry), ("<tag>&9 ! exact", "available"))
        self.set_runtime(90)
        with self.mono(90):
            self.assertEqual(release_secret(self.entry), (None, "locked"))

    def test_repeated_start_cancel_restart_and_cancel_after_eligibility(self):
        _, first, created = self.start(); self.set_runtime(20)
        with self.mono(20):
            _, second, again = start_unlock(self.entry)
        self.assertTrue(created); self.assertFalse(again); self.assertEqual(first.pk, second.pk)
        with self.mono(20): cancel_or_relock(self.entry)
        self.set_runtime(45)
        with self.mono(45): _, restarted, _ = start_unlock(self.entry)
        self.assertEqual(restarted.eligible_runtime_ms, 105000)
        self.set_runtime(105)
        with self.mono(105): cancel_or_relock(self.entry)
        with self.mono(105): self.assertEqual(release_secret(self.entry), (None, "locked"))

    def test_automatic_expires_without_view_and_manual_persists(self):
        self.start(); self.set_runtime(91)
        with self.mono(91): self.assertEqual(release_secret(self.entry), (None, "locked"))
        self.entry.relock_policy="manual"; self.entry.relock_after_seconds=None; self.entry.save()
        self.set_runtime(100)
        with self.mono(100): start_unlock(self.entry)
        self.set_runtime(100000)
        with self.mono(100000): self.assertEqual(release_secret(self.entry)[1], "available")

    def test_wall_clock_jumps_do_not_change_wait_state(self):
        self.start()
        self.set_runtime(10)
        with self.mono(10), patch("vault.services.timezone.now", return_value=timezone.now().replace(year=2099)):
            self.assertEqual(grant_status(self.entry)[0], "waiting")
        with self.mono(10), patch("vault.services.timezone.now", return_value=timezone.now().replace(year=2001)):
            self.assertEqual(grant_status(self.entry)[2], 50000)

    def test_restart_pauses_wait_and_invalidates_automatic_available(self):
        self.start(); self.set_runtime(30)
        with patch("vault.services.time.monotonic_ns", return_value=BASE_NS + 999_000_000_000):
            new_generation = initialize_runtime_clock(generation="new-generation")
        os.environ["PUREVAULT_CLOCK_GENERATION"] = new_generation
        grant = UnlockGrant.objects.get(entry=self.entry)
        self.assertEqual(grant.eligible_runtime_ms - RuntimeClock.objects.get(pk=1).elapsed_milliseconds, 30000)
        self.set_runtime(60)
        os.environ["PUREVAULT_CLOCK_GENERATION"] = new_generation
        initialize_runtime_clock(monotonic_ns=BASE_NS + 2_000_000_000_000, generation="newer-generation")
        self.assertFalse(UnlockGrant.objects.filter(entry=self.entry).exists())

    def test_manual_available_survives_restart(self):
        self.entry.relock_policy="manual"; self.entry.relock_after_seconds=None; self.entry.save()
        self.start(); self.set_runtime(60)
        initialize_runtime_clock(monotonic_ns=BASE_NS + 2_000_000_000_000, generation="manual-restart")
        self.assertTrue(UnlockGrant.objects.filter(entry=self.entry).exists())

    def test_long_gap_gets_no_credit_and_fails_automatic_closed(self):
        self.start(); self.set_runtime(60)
        with self.mono(70):
            snapshot = advance_runtime_clock()
        self.assertTrue(snapshot.continuity_lost)
        self.assertEqual(snapshot.elapsed_ms, 60000)
        self.assertFalse(UnlockGrant.objects.filter(entry=self.entry).exists())

    def test_negative_monotonic_read_never_moves_checkpoint_backward(self):
        self.set_runtime(1)
        before = RuntimeClock.objects.get(pk=1).checkpoint_monotonic_ns
        with self.mono(.5): snapshot = advance_runtime_clock()
        self.assertTrue(snapshot.continuity_lost)
        self.assertEqual(RuntimeClock.objects.get(pk=1).checkpoint_monotonic_ns, before)

    def test_stale_or_missing_generation_fails_closed(self):
        self.start(); self.set_runtime(60)
        os.environ["PUREVAULT_CLOCK_GENERATION"] = "stale"
        with self.mono(60), self.assertRaises(ClockUnavailable): release_secret(self.entry)
        os.environ.pop("PUREVAULT_CLOCK_GENERATION")
        with self.mono(60), self.assertRaises(ClockUnavailable): release_secret(self.entry)

    def test_locked_edit_and_internal_field_tampering_are_rejected(self):
        data={"title":"Changed","password":"replacement","unlock_delay":"1","unlock_unit":"seconds","relock_policy":"manual","relock_after":"1","relock_unit":"seconds"}
        self.assertEqual(self.client.put(f"/api/entries/{self.entry.id}/", data=json.dumps(data), content_type="application/json").status_code,423)
        data["owner"]=self.other.id
        self.assertEqual(self.post("/api/entries/",data).status_code,400)

    def test_invalid_duration_bounds_are_rejected(self):
        for value in ["0","-1","1.5","NaN",str(366*86400)]:
            response=self.post("/api/entries/",{"title":"Example","password":"neutral-test-secret","unlock_delay":value,"unlock_unit":"seconds","relock_policy":"manual"})
            self.assertEqual(response.status_code,400,value)

    def test_edit_rechecks_inside_transaction_and_relocks(self):
        self.start(); self.set_runtime(60)
        with self.mono(60):
            updated=update_entry_if_available(self.entry,self.user,encrypted_secret=encrypt_secret("replacement"),values={"title":"Updated","unlock_delay_seconds":90,"relock_policy":"manual","relock_after_seconds":None})
        self.assertEqual(updated.title,"Updated"); self.assertFalse(UnlockGrant.objects.filter(entry=updated).exists())
        with self.mono(60), self.assertRaises(EntryNotAvailable):
            update_entry_if_available(updated,self.user,encrypted_secret=encrypt_secret("again"),values={"title":"Again","unlock_delay_seconds":1,"relock_policy":"manual","relock_after_seconds":None})

    def test_staff_flags_do_not_bypass(self):
        self.user.is_staff=True; self.user.is_superuser=True; self.user.save()
        with self.mono(0): self.assertEqual(self.post(f"/api/entries/{self.entry.id}/reveal/").status_code,423)


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class TimezoneTests(RuntimeClockMixin, TestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.user=User.objects.create_user("timezone-user",password="neutral-timezone-password")
        self.client=Client(); force_login_active(self.client, self.user)

    def tearDown(self): self.env.stop()

    def test_timezone_persists_and_invalid_name_is_rejected(self):
        response=self.client.post("/api/account/timezone/",data=json.dumps({"display_timezone":"America/New_York"}),content_type="application/json")
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get("/api/session/").json()["display_timezone"],"America/New_York")
        self.assertEqual(UserProfile.objects.get(user=self.user).display_timezone,"America/New_York")
        invalid=self.client.post("/api/account/timezone/",data=json.dumps({"display_timezone":"Not/A_Zone"}),content_type="application/json")
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(UserProfile.objects.get(user=self.user).display_timezone,"America/New_York")

    def test_timezone_change_does_not_touch_grants(self):
        entry=VaultEntry.objects.create(owner=self.user,title="Example",encrypted_secret=b"test",unlock_delay_seconds=60,relock_policy="manual",relock_after_seconds=None)
        grant=UnlockGrant.objects.create(entry=entry,requested_at=timezone.now(),eligible_at=timezone.now(),requested_runtime_ms=0,eligible_runtime_ms=60000)
        self.client.post("/api/account/timezone/",data=json.dumps({"display_timezone":"Europe/Berlin"}),content_type="application/json")
        grant.refresh_from_db(); self.assertEqual(grant.eligible_runtime_ms,60000)


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class SessionInactivityTests(RuntimeClockMixin, TestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.key_dir = tempfile.TemporaryDirectory()
        self.key_override = override_settings(PUREVAULT_KEY_FILE=Path(self.key_dir.name) / "app.key")
        self.key_override.enable()
        self.user = User.objects.create_user("session-user", password="neutral-session-password")
        self.entry = VaultEntry.objects.create(owner=self.user, title="Session example", encrypted_secret=encrypt_secret("session-test-secret"), unlock_delay_seconds=60, relock_policy="manual", relock_after_seconds=None)

    def tearDown(self):
        self.key_override.disable(); self.key_dir.cleanup(); self.env.stop()

    def active_client(self, last_runtime_ms=0, generation=GENERATION):
        client = Client()
        force_login_active(client, self.user, last_runtime_ms, generation)
        return client

    def request_at(self, client, seconds, path="/api/session/", method="get"):
        self.set_runtime(seconds)
        with self.mono(seconds):
            if method == "post":
                return client.post(path, data="{}", content_type="application/json")
            return client.get(path)

    def test_expiry_boundary_just_before_at_and_after_ten_minutes(self):
        before = self.active_client()
        response = self.request_at(before, 599.999)
        self.assertTrue(response.json()["authenticated"])
        at_boundary = self.active_client()
        response = self.request_at(at_boundary, 600)
        self.assertFalse(response.json()["authenticated"])
        self.assertEqual(response.json()["signed_out_reason"], "inactivity")
        after = self.active_client()
        response = self.request_at(after, 601, "/api/entries/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "inactivity_timeout")

    def test_polling_does_not_extend_but_real_activity_does(self):
        idle = self.active_client()
        self.assertEqual(self.request_at(idle, 100, "/api/entries/").status_code, 200)
        self.assertEqual(self.request_at(idle, 500, "/api/accountability/").status_code, 200)
        self.assertEqual(idle.session[SESSION_ACTIVITY_RUNTIME_KEY], 0)
        self.assertEqual(self.request_at(idle, 600, "/api/entries/").status_code, 401)

        active = self.active_client()
        self.assertEqual(self.request_at(active, 500, "/api/session/activity/", "post").status_code, 200)
        self.assertEqual(active.session[SESSION_ACTIVITY_RUNTIME_KEY], 500000)
        self.assertEqual(self.request_at(active, 1099, "/api/entries/").status_code, 200)
        self.assertEqual(self.request_at(active, 1100, "/api/entries/").status_code, 401)

    def test_activity_after_expiry_cannot_revive_session(self):
        client = self.active_client()
        expired = self.request_at(client, 600, "/api/session/activity/", "post")
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(expired.json()["code"], "inactivity_timeout")
        later = self.request_at(client, 601, "/api/session/activity/", "post")
        self.assertEqual(later.status_code, 401)
        self.assertEqual(later.json()["code"], "authentication_required")

    def test_expired_sensitive_request_cannot_reveal(self):
        UnlockGrant.objects.create(entry=self.entry, requested_at=timezone.now(), eligible_at=timezone.now(), requested_runtime_ms=0, eligible_runtime_ms=0)
        client = self.active_client()
        response = self.request_at(client, 600, f"/api/entries/{self.entry.id}/reveal/", "post")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("session-test-secret", response.content.decode())
        self.assertEqual(UnlockGrant.objects.get(entry=self.entry).release_count, 0)

    def test_missing_invalid_and_restart_stale_metadata_fail_closed(self):
        missing = Client(); missing.force_login(self.user)
        with self.mono(0): self.assertEqual(missing.get("/api/entries/").status_code, 401)
        invalid = self.active_client(); session = invalid.session
        session[SESSION_ACTIVITY_RUNTIME_KEY] = "not-a-number"; session.save()
        with self.mono(0): self.assertEqual(invalid.get("/api/entries/").status_code, 401)
        stale = self.active_client(generation="previous-generation")
        with self.mono(0): self.assertEqual(stale.get("/api/entries/").status_code, 401)

    def test_shared_session_activity_and_explicit_logout(self):
        first = self.active_client()
        second = Client(); second.cookies = first.cookies.copy()
        self.assertEqual(self.request_at(first, 500, "/api/session/activity/", "post").status_code, 200)
        self.assertEqual(self.request_at(second, 1099, "/api/entries/").status_code, 200)
        with self.mono(1099): self.assertEqual(first.post("/api/logout/", data="{}", content_type="application/json").status_code, 200)
        with self.mono(1099): self.assertEqual(second.get("/api/entries/").status_code, 401)

    def test_expiry_preserves_grants_deadlines_and_mail_queue(self):
        grant = UnlockGrant.objects.create(entry=self.entry, requested_at=timezone.now(), eligible_at=timezone.now(), requested_runtime_ms=0, eligible_runtime_ms=900000)
        delivery = queue_delivery(owner=self.user, event_id=uuid.uuid4(), event_type=MailDelivery.EventType.TEST, recipient_name="Recipient", recipient_email="recipient@example.com", subject="Purevault: test", body="Synthetic queue record")
        client = self.active_client()
        self.assertEqual(self.request_at(client, 600, "/api/entries/").status_code, 401)
        grant.refresh_from_db(); delivery.refresh_from_db()
        self.assertEqual(grant.eligible_runtime_ms, 900000)
        self.assertEqual(delivery.status, MailDelivery.Status.PENDING)
        self.assertEqual(MailDelivery.objects.count(), 1)

    def test_login_stamps_server_runtime_not_client_input(self):
        client = Client()
        self.set_runtime(123)
        with self.mono(123):
            response = client.post("/api/login/", data=json.dumps({"username": "session-user", "password": "neutral-session-password", "timestamp": 999}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        with self.mono(123):
            response = client.post("/api/login/", data=json.dumps({"username": "session-user", "password": "neutral-session-password"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.session[SESSION_ACTIVITY_RUNTIME_KEY], 123000)
        self.assertEqual(client.session[SESSION_ACTIVITY_GENERATION_KEY], GENERATION)


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class SetupSecurityTests(RuntimeClockMixin, TransactionTestCase):
    def setUp(self):
        self.initialize_test_clock()
        User.objects.all().delete(); InstallationState.objects.update_or_create(pk=1,defaults={"configured":False})

    def tearDown(self): self.env.stop()

    @staticmethod
    def payload(username="first-user",password="strong-neutral-master-password"):
        return {"username":username,"password":password,"password_confirmation":password}

    def post_setup(self,client,payload): return client.post("/api/setup/",data=json.dumps(payload),content_type="application/json")

    def test_setup_second_setup_validation_existing_and_csrf(self):
        bad=self.payload(password="short"); self.assertEqual(self.post_setup(Client(),bad).status_code,400)
        self.assertFalse(InstallationState.objects.get(pk=1).configured)
        mismatch=self.payload(); mismatch["password_confirmation"]="different-neutral-password"
        self.assertEqual(self.post_setup(Client(),mismatch).status_code,400)
        self.assertFalse(InstallationState.objects.get(pk=1).configured)
        first=self.post_setup(Client(),self.payload()); self.assertEqual(first.status_code,201)
        self.assertEqual(self.post_setup(Client(),self.payload("second-user","another-strong-neutral-password")).status_code,409)
        User.objects.all().delete(); InstallationState.objects.filter(pk=1).update(configured=False)
        existing=User.objects.create_user("existing-user",password="existing-neutral-password")
        self.assertEqual(self.post_setup(Client(),self.payload()).status_code,409); self.assertTrue(User.objects.filter(pk=existing.pk).exists())
        User.objects.all().delete(); InstallationState.objects.filter(pk=1).update(configured=False)
        csrf=Client(enforce_csrf_checks=True); self.assertEqual(self.post_setup(csrf,self.payload()).status_code,403)
        csrf.get("/api/session/")
        accepted=csrf.post("/api/setup/",data=json.dumps(self.payload()),content_type="application/json",HTTP_X_CSRFTOKEN=csrf.cookies["csrftoken"].value)
        self.assertEqual(accepted.status_code,201)

    def test_concurrent_setup_has_one_winner(self):
        barrier=Barrier(2)
        def attempt(name):
            close_old_connections(); barrier.wait()
            try: create_initial_user(name,"concurrent-neutral-password"); return "created"
            except SetupAlreadyComplete: return "complete"
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool: outcomes=list(pool.map(attempt,["one-user","two-user"]))
        self.assertCountEqual(outcomes,["created","complete"]); self.assertEqual(User.objects.count(),1)


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class RuntimeConcurrencyTests(RuntimeClockMixin, TransactionTestCase):
    def setUp(self): self.initialize_test_clock()
    def tearDown(self): self.env.stop()

    def test_concurrent_cas_credits_interval_once(self):
        barrier=Barrier(8)
        def advance(_):
            close_old_connections(); barrier.wait()
            try: return advance_runtime_clock(_monotonic_ns_provider=lambda:BASE_NS+1_000_000_000).elapsed_ms
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(advance,range(8)))
        clock=RuntimeClock.objects.get(pk=1)
        self.assertEqual(clock.elapsed_milliseconds,1000)
        self.assertEqual(clock.checkpoint_monotonic_ns,BASE_NS+1_000_000_000)

    def test_failed_cas_retry_resamples_newer_monotonic_time(self):
        readings=iter([BASE_NS+1_000_000_000,BASE_NS+2_000_000_000])
        original_update=QuerySet.update
        calls={"count":0}
        def fail_first(queryset,**kwargs):
            calls["count"]+=1
            if calls["count"]==1: return 0
            return original_update(queryset,**kwargs)
        with patch.object(QuerySet,"update",autospec=True,side_effect=fail_first):
            snapshot=advance_runtime_clock(_monotonic_ns_provider=lambda:next(readings))
        clock=RuntimeClock.objects.get(pk=1)
        self.assertEqual(snapshot.elapsed_ms,2000)
        self.assertEqual(clock.elapsed_milliseconds,2000)
        self.assertEqual(clock.checkpoint_monotonic_ns,BASE_NS+2_000_000_000)

    @override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=["vault.example.com"])
    def test_health_remains_available_with_documented_https_settings(self):
        with patch("vault.views.time.monotonic_ns",return_value=BASE_NS):
            response=Client().get("/health/",HTTP_HOST="vault.example.com")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{"status":"ok"})


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class AccountabilityTests(RuntimeClockMixin, TestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.key_dir = tempfile.TemporaryDirectory()
        self.key_override = override_settings(PUREVAULT_KEY_FILE=Path(self.key_dir.name) / "app.key")
        self.key_override.enable()
        self.user = User.objects.create_user("account-owner", password="long-neutral-test-password")
        self.other = User.objects.create_user("other-owner", password="other-neutral-test-password")
        self.entry = VaultEntry.objects.create(owner=self.user, title="Example <entry>", encrypted_secret=encrypt_secret("never-email-this-secret"), unlock_delay_seconds=60, relock_policy="manual", relock_after_seconds=None)
        self.client = Client(); force_login_active(self.client, self.user)

    def tearDown(self):
        self.key_override.disable(); self.key_dir.cleanup(); self.env.stop()

    def post(self, path, data=None):
        return self.client.post(path, data=json.dumps(data or {}), content_type="application/json")

    def partner(self, email="partner@example.com", name="Example Partner"):
        return AccountabilityPartner.objects.create(owner=self.user, name=name, email=email)

    def test_reason_required_and_bounded_only_when_partners_exist(self):
        self.partner()
        for data in [{}, {"reason": "   "}, {"reason": "x" * 1001}]:
            with self.mono(0): response = self.post(f"/api/entries/{self.entry.id}/unlock/", data)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(UnlockGrant.objects.filter(entry=self.entry).exists())
        AccountabilityPartner.objects.all().delete()
        with self.mono(0): response = self.post(f"/api/entries/{self.entry.id}/unlock/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["created"])

    def test_unlock_snapshots_each_recipient_and_never_secret(self):
        self.partner("one@example.com", "One"); self.partner("two@example.com", "Two")
        with self.mono(0): response = self.post(f"/api/entries/{self.entry.id}/unlock/", {"reason": "A neutral reason <b>not HTML</b>"})
        self.assertEqual(response.status_code, 200)
        deliveries = list(MailDelivery.objects.filter(event_type=MailDelivery.EventType.UNLOCK_REQUESTED))
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(len({item.event_id for item in deliveries}), 1)
        self.assertCountEqual([item.recipient_email for item in deliveries], ["one@example.com", "two@example.com"])
        payload = decrypt_text(deliveries[0].encrypted_payload)
        self.assertIn("Example <entry>", payload)
        self.assertIn("A neutral reason <b>not HTML</b>", payload)
        self.assertNotIn("never-email-this-secret", payload)
        self.assertNotIn("never-email-this-secret", response.content.decode())

    def test_repeat_is_idempotent_and_cancel_restart_creates_new_event(self):
        self.partner()
        with self.mono(0): first = self.post(f"/api/entries/{self.entry.id}/unlock/", {"reason": "First request"})
        self.assertTrue(first.json()["created"])
        with self.mono(10): repeated = self.post(f"/api/entries/{self.entry.id}/unlock/", {})
        self.assertFalse(repeated.json()["created"])
        self.assertEqual(MailDelivery.objects.filter(event_type="unlock_requested").count(), 1)
        with self.mono(10): self.post(f"/api/entries/{self.entry.id}/cancel/")
        self.assertEqual(MailDelivery.objects.filter(event_type="unlock_requested").count(), 1)
        self.set_runtime(20)
        with self.mono(20): restarted = self.post(f"/api/entries/{self.entry.id}/unlock/", {"reason": "Second request"})
        self.assertTrue(restarted.json()["created"])
        self.assertEqual(MailDelivery.objects.filter(event_type="unlock_requested").count(), 2)
        self.assertEqual(MailDelivery.objects.filter(event_type="unlock_requested").values("event_id").distinct().count(), 2)

    def test_grant_and_outbox_roll_back_together(self):
        self.partner()
        with self.mono(0), patch("vault.services.queue_delivery", side_effect=RuntimeError("synthetic failure")), self.assertRaises(RuntimeError):
            start_unlock(self.entry, "Reason")
        self.assertFalse(UnlockGrant.objects.filter(entry=self.entry).exists())
        self.assertFalse(MailDelivery.objects.exists())

    def test_partner_owner_duplicate_reauth_and_csrf(self):
        other_partner = AccountabilityPartner.objects.create(owner=self.other, name="Other", email="other@example.com")
        self.assertEqual(self.client.get("/api/accountability/").json()["partners"], [])
        bad = {"name": "Partner", "email": "PARTNER@example.com", "current_password": "wrong"}
        self.assertEqual(self.post("/api/accountability/partners/", bad).status_code, 403)
        good = {**bad, "current_password": "long-neutral-test-password"}
        self.assertEqual(self.post("/api/accountability/partners/", good).status_code, 201)
        self.assertEqual(AccountabilityPartner.objects.get(owner=self.user).email, "partner@example.com")
        self.assertEqual(self.post("/api/accountability/partners/", good).status_code, 400)
        self.assertEqual(self.client.delete(f"/api/accountability/partners/{other_partner.id}/", data=json.dumps({"current_password": "long-neutral-test-password"}), content_type="application/json").status_code, 404)
        csrf = Client(enforce_csrf_checks=True); force_login_active(csrf, self.user)
        self.assertEqual(csrf.post("/api/accountability/partners/", data=json.dumps(good), content_type="application/json").status_code, 403)

    def test_partner_change_and_removal_notices_survive_deletion(self):
        partner = self.partner()
        changed = self.client.put(f"/api/accountability/partners/{partner.id}/", data=json.dumps({"name": "Updated", "email": "new@example.com", "current_password": "long-neutral-test-password"}), content_type="application/json")
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(MailDelivery.objects.filter(event_type="partner_address_changed", recipient_email="partner@example.com").exists())
        self.assertTrue(MailDelivery.objects.filter(event_type="partner_added", recipient_email="new@example.com").exists())
        removed = self.client.delete(f"/api/accountability/partners/{partner.id}/", data=json.dumps({"current_password": "long-neutral-test-password"}), content_type="application/json")
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(AccountabilityPartner.objects.filter(pk=partner.pk).exists())
        self.assertTrue(MailDelivery.objects.filter(event_type="partner_removed", recipient_email="new@example.com").exists())

    def test_smtp_password_encrypted_masked_and_validation(self):
        payload = {"enabled": True, "host": "smtp.example.com", "port": 587, "security": "starttls", "username": "mailer", "password_action": "replace", "password": "synthetic-smtp-password", "from_email": "vault@example.com", "from_name": "Purevault", "current_password": "long-neutral-test-password"}
        response = self.client.put("/api/accountability/email/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        configuration = EmailSettings.objects.get(owner=self.user)
        self.assertNotIn(b"synthetic-smtp-password", bytes(configuration.encrypted_password))
        for body in [response.content, self.client.get("/api/accountability/").content]:
            self.assertNotIn(b"synthetic-smtp-password", body); self.assertNotIn(b"encrypted_password", body)
        self.assertTrue(response.json()["email"]["password_set"])
        half = {**payload, "username": "mailer", "password_action": "clear", "password": ""}
        self.assertEqual(self.client.put("/api/accountability/email/", data=json.dumps(half), content_type="application/json").status_code, 400)
        injected = {**half, "username": "", "host": "smtp.example.com\r\nBcc: attacker@example.com"}
        self.assertEqual(self.client.put("/api/accountability/email/", data=json.dumps(injected), content_type="application/json").status_code, 400)

    def test_mail_failure_does_not_change_unlock_timing(self):
        self.partner()
        with self.mono(0): self.post(f"/api/entries/{self.entry.id}/unlock/", {"reason": "Timing remains independent"})
        delivery, token = claim_due_delivery()
        process_claimed_delivery(delivery, token)
        self.set_runtime(60)
        with self.mono(60):
            self.assertEqual(grant_status(self.entry)[0], "available")
            self.assertEqual(release_secret(self.entry)[0], "never-email-this-secret")

    def test_configuration_change_notice_prefers_previous_transport(self):
        self.partner()
        first = {"enabled": True, "host": "old.example.com", "port": 587, "security": "starttls", "username": "mailer", "password_action": "replace", "password": "old-synthetic-password", "from_email": "vault@example.com", "from_name": "Purevault", "current_password": "long-neutral-test-password"}
        self.assertEqual(self.client.put("/api/accountability/email/", data=json.dumps(first), content_type="application/json").status_code, 200)
        MailDelivery.objects.all().delete()
        delayed = queue_delivery(owner=self.user, event_id=uuid.uuid4(), event_type=MailDelivery.EventType.TEST, recipient_name="Recipient", recipient_email="recipient@example.com", subject="Purevault: test", body="Synthetic test")
        MailDelivery.objects.filter(pk=delayed.pk).update(next_attempt_at=timezone.now() + timedelta(hours=1), needs_attention=True, last_error="Synthetic prior failure")
        second = {**first, "host": "new.example.com", "password_action": "keep", "password": ""}
        self.assertEqual(self.client.put("/api/accountability/email/", data=json.dumps(second), content_type="application/json").status_code, 200)
        delayed.refresh_from_db()
        self.assertLessEqual(delayed.next_attempt_at, timezone.now())
        self.assertFalse(delayed.needs_attention)
        self.assertEqual(delayed.last_error, "")
        notice = MailDelivery.objects.get(event_type="smtp_configuration_changed")
        snapshot = decrypt_text(notice.encrypted_transport_snapshot)
        self.assertIn("old.example.com", snapshot)
        self.assertNotIn("new.example.com", snapshot)


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class UnlockMailConcurrencyTests(RuntimeClockMixin, TransactionTestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.key_dir = tempfile.TemporaryDirectory()
        self.key_override = override_settings(PUREVAULT_KEY_FILE=Path(self.key_dir.name) / "app.key")
        self.key_override.enable()
        self.user = User.objects.create_user("concurrent-owner", password="neutral-concurrent-password")
        self.entry = VaultEntry.objects.create(owner=self.user, title="Concurrent example", encrypted_secret=encrypt_secret("synthetic-secret"), unlock_delay_seconds=60, relock_policy="manual", relock_after_seconds=None)
        AccountabilityPartner.objects.create(owner=self.user, name="Partner", email="partner@example.com")

    def tearDown(self):
        self.key_override.disable(); self.key_dir.cleanup(); self.env.stop()

    def test_concurrent_starts_create_one_grant_and_one_event(self):
        barrier = Barrier(2)
        def attempt(reason):
            close_old_connections(); barrier.wait()
            try:
                with patch("vault.services.time.monotonic_ns", return_value=BASE_NS):
                    return start_unlock(VaultEntry.objects.get(pk=self.entry.pk), reason)[2]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ["First reason", "Second reason"]))
        self.assertCountEqual(outcomes, [True, False])
        self.assertEqual(UnlockGrant.objects.filter(entry=self.entry).count(), 1)
        self.assertEqual(MailDelivery.objects.filter(event_type="unlock_requested").count(), 1)


@override_settings(PUREVAULT_DJANGO_SECRET_KEY="test-only")
class MailDeliveryTests(RuntimeClockMixin, TestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.key_dir = tempfile.TemporaryDirectory()
        self.key_override = override_settings(PUREVAULT_KEY_FILE=Path(self.key_dir.name) / "app.key")
        self.key_override.enable()
        self.user = User.objects.create_user("mail-owner", password="neutral-mail-password")

    def tearDown(self):
        self.key_override.disable(); self.key_dir.cleanup(); self.env.stop()

    def configure(self, security="starttls"):
        return EmailSettings.objects.create(owner=self.user, enabled=True, host="smtp.example.com", port=587, security=security, username="mailer", encrypted_password=encrypt_text("synthetic-password"), from_email="vault@example.com", from_name="Purevault")

    def delivery(self):
        return queue_delivery(owner=self.user, event_id=uuid.uuid4(), event_type=MailDelivery.EventType.TEST, recipient_name="Recipient", recipient_email="recipient@example.com", subject="Purevault: test", body="Plain <b>text</b> only")

    def test_smtp_modes_tls_verification_and_plain_text(self):
        delivery = self.delivery()
        for security, constructor, starttls in [("starttls", "SMTP", True), ("none", "SMTP", False), ("ssl", "SMTP_SSL", False)]:
            transport = {"host": "smtp.example.com", "port": 587, "security": security, "username": "mailer", "password": "synthetic-password", "from_email": "vault@example.com", "from_name": "Purevault"}
            client = MagicMock(); client.__enter__.return_value = client
            with patch(f"vault.mail_service.smtplib.{constructor}", return_value=client) as factory:
                _send_smtp(delivery, transport)
            self.assertTrue(factory.called)
            self.assertEqual(client.starttls.called, starttls)
            message = client.send_message.call_args.args[0]
            self.assertEqual(message.get_content_type(), "text/plain")
            self.assertIn("Plain <b>text</b> only", message.get_content())
            self.assertEqual(message["To"].addresses[0].addr_spec, "recipient@example.com")

    def test_failure_retries_then_configuration_fix_accepts(self):
        delivery = self.delivery()
        claimed, token = claim_due_delivery()
        self.assertEqual(claimed.pk, delivery.pk)
        process_claimed_delivery(claimed, token)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, MailDelivery.Status.PENDING)
        self.assertTrue(delivery.needs_attention)
        self.assertEqual(delivery.attempt_count, 1)
        self.configure()
        MailDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
        claimed, token = claim_due_delivery()
        process_claimed_delivery(claimed, token, sender=lambda item, transport: None)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, MailDelivery.Status.ACCEPTED)
        self.assertEqual(delivery.attempt_count, 2)

    def test_timeout_backoff_and_expired_lease_recovery(self):
        self.configure(); delivery = self.delivery()
        claimed, token = claim_due_delivery()
        process_claimed_delivery(claimed, token, sender=lambda item, transport: (_ for _ in ()).throw(TimeoutError()))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, MailDelivery.Status.PENDING)
        self.assertGreater(delivery.next_attempt_at, timezone.now())
        self.assertFalse(delivery.needs_attention)
        MailDelivery.objects.filter(pk=delivery.pk).update(status=MailDelivery.Status.SENDING, next_attempt_at=timezone.now(), lease_expires_at=timezone.now() - timedelta(seconds=1), lease_token=uuid.uuid4())
        recovered, new_token = claim_due_delivery()
        self.assertEqual(recovered.pk, delivery.pk)
        self.assertEqual(recovered.lease_token, new_token)

    def test_test_email_rate_limit_and_sanitized_provider_error(self):
        self.configure(); client = Client(); force_login_active(client, self.user)
        for index in range(3):
            response = client.post("/api/accountability/email/test/", data=json.dumps({"destination": f"recipient{index}@example.com", "current_password": "neutral-mail-password"}), content_type="application/json")
            self.assertEqual(response.status_code, 202)
        limited = client.post("/api/accountability/email/test/", data=json.dumps({"destination": "fourth@example.com", "current_password": "neutral-mail-password"}), content_type="application/json")
        self.assertEqual(limited.status_code, 429)
        delivery = MailDelivery.objects.first()
        MailDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
        claimed, token = claim_due_delivery()
        process_claimed_delivery(claimed, token, sender=lambda item, transport: (_ for _ in ()).throw(smtplib.SMTPAuthenticationError(535, b"private provider response")))
        claimed.refresh_from_db()
        self.assertNotIn("private provider response", claimed.last_error)
        self.assertIn("authentication failed", claimed.last_error.lower())
