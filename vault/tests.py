import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.db.models.query import QuerySet
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from vault.crypto import encrypt_secret
from vault.models import InstallationState, RuntimeClock, UnlockGrant, UserProfile, VaultEntry
from vault.services import (
    ClockUnavailable, SetupAlreadyComplete, advance_runtime_clock, cancel_or_relock,
    create_initial_user, grant_status, initialize_runtime_clock, release_secret,
    start_unlock, update_entry_if_available, EntryNotAvailable,
)


BASE_NS = 1_000_000_000_000
GENERATION = "00000000-0000-0000-0000-000000000001"


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
        self.client = Client(); self.client.force_login(self.user)

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
        self.client.force_login(self.other)
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
class TimezoneTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user("timezone-user",password="neutral-timezone-password")
        self.client=Client(); self.client.force_login(self.user)

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
class SetupSecurityTests(TransactionTestCase):
    def setUp(self):
        User.objects.all().delete(); InstallationState.objects.update_or_create(pk=1,defaults={"configured":False})

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
