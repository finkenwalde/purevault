import json
import os
import tempfile
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.db.models.query import QuerySet
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from vault import backups
from vault.backup_format import InvalidBackup, create_recovery_bundle
from vault.crypto import encrypt_secret
from vault.models import BackupSettings, InstallationState, RuntimeClock, UnlockGrant, UserProfile, VaultEntry
from vault.session_activity import SESSION_ACTIVITY_GENERATION_KEY, SESSION_ACTIVITY_RUNTIME_KEY
from vault.services import (
    ClockUnavailable, SetupAlreadyComplete, advance_runtime_clock, cancel_or_relock,
    create_initial_user, grant_status, initialize_runtime_clock, release_secret,
    start_unlock,
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

    def test_unlock_api_needs_no_reason_and_old_mail_routes_are_gone(self):
        path = f"/api/entries/{self.entry.id}/unlock/"
        with self.mono(0):
            self.assertEqual(self.post(path, {"reason": "obsolete field"}).status_code, 400)
            self.assertFalse(UnlockGrant.objects.filter(entry=self.entry).exists())
            response = self.post(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["entry"]["status"], "waiting")
            for suffix in ("", "partners/", "partners/1/", "email/", "email/test/"):
                self.assertEqual(self.client.get(f"/api/accountability/{suffix}").status_code, 404)

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
        self.entry.relock_policy = "manual"
        self.entry.relock_after_seconds = None
        self.entry.save(update_fields=["relock_policy", "relock_after_seconds"])
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

    def test_waiting_edit_cannot_shortcut_the_original_delay(self):
        self.start(); self.set_runtime(30)
        data={"title":"Changed","password":"","unlock_delay":"1","unlock_unit":"seconds","relock_policy":"manual","relock_after":"1","relock_unit":"seconds","relock":False}
        with self.mono(30):
            response = self.client.put(f"/api/entries/{self.entry.id}/", data=json.dumps(data), content_type="application/json")
        self.assertEqual(response.status_code, 423)
        grant = UnlockGrant.objects.get(entry=self.entry)
        self.assertEqual(grant.eligible_runtime_ms, 60000)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.unlock_delay_seconds, 60)

    def test_invalid_duration_bounds_are_rejected(self):
        for value in ["0","-1","1.5","NaN",str(366*86400)]:
            response=self.post("/api/entries/",{"title":"Example","password":"neutral-test-secret","unlock_delay":value,"unlock_unit":"seconds","relock_policy":"manual"})
            self.assertEqual(response.status_code,400,value)

    def test_edit_save_preserves_grant_and_unchanged_password(self):
        self.start(); self.set_runtime(60)
        with self.mono(60):
            response = self.client.put(f"/api/entries/{self.entry.id}/", data=json.dumps({"title":"Updated","password":"","unlock_delay":"90","unlock_unit":"seconds","relock_policy":"manual","relock_after":"1","relock_unit":"seconds","relock":False}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        updated = VaultEntry.objects.get(pk=self.entry.pk)
        grant = UnlockGrant.objects.get(entry=updated)
        self.assertEqual(updated.title, "Updated")
        self.assertEqual(grant.access_until_runtime_ms, 90000)
        self.assertEqual(response.json()["entry"]["status"], "available")
        with self.mono(60): self.assertEqual(release_secret(updated)[0], "<tag>&9 ! exact")
        self.set_runtime(90)
        with self.mono(90):
            self.assertEqual(release_secret(updated), (None, "locked"))
            expired_edit = self.client.put(f"/api/entries/{updated.id}/", data=json.dumps({"title":"Too late","password":"","unlock_delay":"1","unlock_unit":"seconds","relock_policy":"manual","relock_after":"1","relock_unit":"seconds","relock":False}), content_type="application/json")
            self.assertEqual(expired_edit.status_code, 423)
            self.assertEqual(self.post(f"/api/entries/{updated.id}/reveal/").status_code, 423)

    def test_edit_save_and_relock_is_atomic_and_replacement_is_optional(self):
        self.start(); self.set_runtime(60)
        with self.mono(60):
            response = self.client.put(f"/api/entries/{self.entry.id}/", data=json.dumps({"title":"Updated","password":"replacement","unlock_delay":"2","unlock_unit":"minutes","relock_policy":"automatic","relock_after":"45","relock_unit":"seconds","relock":True}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entry"]["status"], "locked")
        self.assertFalse(UnlockGrant.objects.filter(entry=self.entry).exists())
        self.set_runtime(61)
        with self.mono(61):
            self.assertEqual(start_unlock(VaultEntry.objects.get(pk=self.entry.pk))[1].eligible_runtime_ms, 181000)
        self.set_runtime(181)
        with self.mono(181):
            self.assertEqual(release_secret(VaultEntry.objects.get(pk=self.entry.pk))[0], "replacement")

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

    def test_expiry_preserves_grant_deadline(self):
        grant = UnlockGrant.objects.create(entry=self.entry, requested_at=timezone.now(), eligible_at=timezone.now(), requested_runtime_ms=0, eligible_runtime_ms=900000)
        client = self.active_client()
        self.assertEqual(self.request_at(client, 600, "/api/entries/").status_code, 401)
        grant.refresh_from_db()
        self.assertEqual(grant.eligible_runtime_ms, 900000)

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
class UnlockConcurrencyTests(RuntimeClockMixin, TransactionTestCase):
    def setUp(self):
        self.initialize_test_clock()
        self.key_dir = tempfile.TemporaryDirectory()
        self.key_override = override_settings(PUREVAULT_KEY_FILE=Path(self.key_dir.name) / "app.key")
        self.key_override.enable()
        self.user = User.objects.create_user("concurrent-owner", password="neutral-concurrent-password")
        self.entry = VaultEntry.objects.create(owner=self.user, title="Concurrent example", encrypted_secret=encrypt_secret("synthetic-secret"), unlock_delay_seconds=60, relock_policy="manual", relock_after_seconds=None)

    def tearDown(self):
        self.key_override.disable(); self.key_dir.cleanup(); self.env.stop()

    def test_concurrent_starts_create_one_grant(self):
        barrier = Barrier(2)
        def attempt(_):
            close_old_connections(); barrier.wait()
            try:
                with patch("vault.services.time.monotonic_ns", return_value=BASE_NS):
                    return start_unlock(VaultEntry.objects.get(pk=self.entry.pk))[2]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        self.assertCountEqual(outcomes, [True, False])
        self.assertEqual(UnlockGrant.objects.filter(entry=self.entry).count(), 1)


class MasterPasswordChangeTests(RuntimeClockMixin, TestCase):
    current = "neutral primary password phrase"
    replacement = "neutral replacement password phrase"
    path = "/api/account/password/"

    def setUp(self):
        self.initialize_test_clock()
        self.addCleanup(self.env.stop)
        self.user = User.objects.create_user("example-password-owner", password=self.current)
        self.client = Client()
        force_login_active(self.client, self.user)

    def change(self, client=None, **overrides):
        client = client if client is not None else self.client
        data = {"current_password": self.current, "new_password": self.replacement, **overrides}
        with self.mono(0):
            return client.post(self.path, data, content_type="application/json")

    def test_authentication_csrf_and_invalid_password_payloads(self):
        original = self.user.password
        csrf_client = Client(enforce_csrf_checks=True)
        force_login_active(csrf_client, self.user)
        self.assertEqual(self.change(Client()).status_code, 401)
        self.assertEqual(self.change(csrf_client).status_code, 403)
        self.assertEqual(self.change(current_password="incorrect sample").status_code, 403)
        invalid = [
            {"current_password": None},
            {"new_password": 42},
            {"new_password": ""},
            {"new_password": "x" * 4097},
            {"new_password": "short"},
            {"new_password": "\ud800"},
            {"new_password_confirmation": False},
            {"new_password_confirmation": "different replacement phrase"},
        ]
        for fields in invalid:
            with self.subTest(fields=tuple(fields)):
                self.assertEqual(self.change(**fields).status_code, 400)
        with self.mono(0):
            self.assertEqual(self.client.post(self.path, {}, content_type="application/json").status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, original)

    def test_change_preserves_current_session_grants_encryption_and_backups(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        key_file = directory / "vault.key"
        self.enterContext(override_settings(PUREVAULT_KEY_FILE=key_file))
        entry = VaultEntry.objects.create(
            owner=self.user, title="Example credential", encrypted_secret=encrypt_secret("synthetic secret"),
            unlock_delay_seconds=45, relock_policy="automatic", relock_after_seconds=60,
        )
        with self.mono(0):
            start_unlock(entry)
        BackupSettings.objects.create(owner=self.user, public_key=b"test public", recovery_bundle=b"test wrapped")
        original_entry = VaultEntry.objects.values().get(pk=entry.pk)
        original_grant = UnlockGrant.objects.values().get(entry=entry)
        original_backup = BackupSettings.objects.values().get(owner=self.user)
        original_key = key_file.read_bytes()
        other = Client()
        force_login_active(other, self.user)
        session_key = self.client.session.session_key

        self.assertEqual(self.change(new_password_confirmation=self.replacement).status_code, 200)
        self.assertNotEqual(self.client.session.session_key, session_key)
        self.assertEqual(self.client.session[SESSION_ACTIVITY_RUNTIME_KEY], 0)
        with self.mono(0):
            self.assertEqual(self.client.get("/api/entries/").status_code, 200)
            self.assertEqual(other.get("/api/entries/").status_code, 401)
            fresh = Client()
            for password, code in ((self.current, 401), (self.replacement, 200)):
                response = fresh.post("/api/login/", {
                    "username": self.user.username, "password": password,
                }, content_type="application/json")
                self.assertEqual(response.status_code, code)
        self.assertEqual(VaultEntry.objects.values().get(pk=entry.pk), original_entry)
        self.assertEqual(UnlockGrant.objects.values().get(entry=entry), original_grant)
        self.assertEqual(BackupSettings.objects.values().get(owner=self.user), original_backup)
        self.assertEqual(key_file.read_bytes(), original_key)

    def test_stale_request_cannot_overwrite_concurrent_change(self):
        winner = make_password("different winner password phrase")

        def hash_after_competing_write(password):
            User.objects.filter(pk=self.user.pk).update(password=winner)
            return make_password(password)

        with patch("vault.views.make_password", side_effect=hash_after_competing_write):
            self.assertEqual(self.change().status_code, 409)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, winner)

    def test_previous_api_payload_still_works_without_confirmation(self):
        self.assertEqual(self.change().status_code, 200)


class BackupTests(RuntimeClockMixin, TestCase):
    phrase = "sundial forest mint compass velvet"
    master = "neutral master sample phrase"

    def setUp(self):
        self.initialize_test_clock()
        self.addCleanup(self.env.stop)
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.enterContext(override_settings(DATA_DIR=self.root, PUREVAULT_KEY_FILE=self.root / "source.key"))
        self.user = User.objects.create_user("source-example", password=self.master)
        self.client = Client()
        force_login_active(self.client, self.user)
        self.entry = VaultEntry.objects.create(
            owner=self.user, title="Vault test title", encrypted_secret=encrypt_secret("example secret"),
            unlock_delay_seconds=30, relock_policy="automatic", relock_after_seconds=60,
        )
        UserProfile.objects.create(user=self.user, display_timezone="Asia/Tokyo")

    def configure(self):
        return backups.configure(self.user.pk, 1, 1, create_recovery_bundle(self.phrase))

    def submit_restore(self, client, data, phrase=None, master=None):
        with self.mono(0):
            return client.post("/api/backups/restore/", {
                "backup": SimpleUploadedFile("example.pvbackup", data),
                "passphrase": phrase or self.phrase,
                "current_password": master or self.master,
            })

    def test_schedule_retains_previous_file_after_failed_durable_publish(self):
        self.configure()
        self.assertTrue(backups.run_backup(self.user.pk))
        old, = backups.backup_files(self.user.pk)
        due = timezone.now() - timedelta(days=8)
        BackupSettings.objects.filter(owner=self.user).update(next_run_at=due)
        with patch("vault.backup_format._wrapping_key", side_effect=AssertionError("requires decryption")), \
             patch("vault.backups._sync_directory", side_effect=OSError("synthetic failure")):
            with self.assertRaises(InvalidBackup):
                backups.run_backup(self.user.pk, scheduled=True)
        self.assertEqual(backups.backup_files(self.user.pk), [old])
        self.assertTrue(BackupSettings.objects.get(owner=self.user).last_error)

        BackupSettings.objects.filter(owner=self.user).update(next_run_at=due)
        with patch("vault.backup_format._wrapping_key", side_effect=AssertionError("requires decryption")):
            self.assertTrue(backups.run_backup(self.user.pk, scheduled=True))
            self.assertFalse(backups.run_backup(self.user.pk, scheduled=True))
        new, = backups.backup_files(self.user.pk)
        self.assertNotEqual(new, old)
        self.assertFalse(old.exists())
        self.assertGreater(BackupSettings.objects.get(owner=self.user).next_run_at, timezone.now())

    def test_portable_restore_is_atomic_and_starts_a_full_locked_wait(self):
        self.configure()
        with self.mono(0):
            start_unlock(self.entry)
        backups.run_backup(self.user.pk)
        path, = backups.backup_files(self.user.pk)
        with self.mono(0):
            download = self.client.get(f"/api/backups/files/{path.name}/")
        self.assertEqual(download.status_code, 200)
        self.assertIn("no-store", download["Cache-Control"])
        data = b"".join(download.streaming_content)
        download.close()
        self.assertNotIn(b"Vault test title", data)
        self.assertNotIn(b"example secret", data)
        self.assertEqual(self.submit_restore(self.client, data).status_code, 409)

        target = User.objects.create_user("target-example", password=self.master)
        client = Client()
        force_login_active(client, target)
        (self.root / "target.key").write_bytes(Fernet.generate_key())
        with override_settings(PUREVAULT_KEY_FILE=self.root / "target.key"):
            self.assertEqual(self.submit_restore(client, data, phrase="wrong recovery phrase").status_code, 400)
            self.assertEqual(self.submit_restore(client, data[:-1] + bytes([data[-1] ^ 1])).status_code, 400)
            self.assertEqual(self.submit_restore(client, data, master="wrong master phrase").status_code, 403)
            self.assertFalse(UserProfile.objects.filter(user=target).exists())
            self.assertFalse(VaultEntry.objects.filter(owner=target).exists())
            self.assertEqual(self.submit_restore(client, data).json(), {"restored": 1})
            entry = VaultEntry.objects.get(owner=target)
            self.assertNotEqual(entry.encrypted_secret, self.entry.encrypted_secret)
            self.assertEqual((entry.unlock_delay_seconds, entry.relock_after_seconds), (30, 60))
            self.assertEqual(UserProfile.objects.get(user=target).display_timezone, "Asia/Tokyo")
            self.assertFalse(BackupSettings.objects.filter(owner=target).exists())
            with self.mono(0):
                self.assertEqual(grant_status(entry)[0], "locked")
                self.assertEqual(start_unlock(entry)[1].eligible_runtime_ms, 30000)
            self.set_runtime(29.999)
            with self.mono(29.999):
                self.assertEqual(release_secret(entry)[1], "waiting")
            self.set_runtime(30)
            with self.mono(30):
                self.assertEqual(release_secret(entry), ("example secret", "available"))

    def test_backup_endpoints_require_auth_csrf_and_master_for_recovery_setup(self):
        data = {"passphrase": self.phrase, "confirmation": self.phrase, "interval_days": 0, "retention": 7}
        csrf = Client(enforce_csrf_checks=True)
        force_login_active(csrf, self.user)
        with self.mono(0):
            self.assertEqual(Client().get("/api/backups/").status_code, 401)
            self.assertEqual(csrf.put("/api/backups/", data, content_type="application/json").status_code, 403)
            self.assertEqual(self.client.put("/api/backups/", data, content_type="application/json").status_code, 403)
            self.assertEqual(self.client.put("/api/backups/", {**data, "current_password": self.master}, content_type="application/json").status_code, 200)
            self.assertEqual(csrf.post("/api/backups/run/", "{}", content_type="application/json").status_code, 403)
            self.assertEqual(self.submit_restore(csrf, b"example").status_code, 403)
            oversized = csrf.generic("POST", "/api/backups/restore/", b"x", CONTENT_LENGTH=str(20 * 1024 * 1024))
            self.assertEqual(oversized.status_code, 413)
