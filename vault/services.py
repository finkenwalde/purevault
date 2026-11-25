from dataclasses import dataclass
from datetime import timedelta
import os
import time
import uuid

from django.contrib.auth.models import User
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from vault.crypto import decrypt_secret
from vault.mail_service import clean_reason, queue_delivery, unlock_email
from vault.models import AccountabilityPartner, InstallationState, MailDelivery, RuntimeClock, UnlockGrant, VaultEntry


CLOCK_CHECKPOINT_INTERVAL_SECONDS = 0.25
MAX_TRUSTED_CLOCK_GAP_MS = 2_000


class SetupAlreadyComplete(Exception):
    pass


class EntryNotAvailable(Exception):
    pass


class ClockUnavailable(Exception):
    pass


class MailRateLimited(Exception):
    pass


@dataclass(frozen=True)
class ClockSnapshot:
    elapsed_ms: int
    continuity_lost: bool = False


def setup_is_complete():
    return InstallationState.objects.filter(pk=1, configured=True).exists() or User.objects.exists()


def create_initial_user(username, password):
    for attempt in range(20):
        try:
            with transaction.atomic():
                claimed = InstallationState.objects.filter(pk=1, configured=False).update(configured=True)
                if not claimed or User.objects.exists():
                    raise SetupAlreadyComplete
                return User.objects.create_user(username=username, password=password, is_staff=False, is_superuser=False)
        except OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 19:
                raise
            time.sleep(0.025)
    raise SetupAlreadyComplete


def initialize_runtime_clock(*, monotonic_ns=None, generation=None):
    now_ns = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
    generation = generation or str(uuid.uuid4())
    with transaction.atomic():
        clock, _ = RuntimeClock.objects.get_or_create(pk=1)
        elapsed = clock.elapsed_milliseconds
        UnlockGrant.objects.filter(eligible_runtime_ms__isnull=True).delete()
        UnlockGrant.objects.filter(
            access_until_runtime_ms__isnull=False,
            eligible_runtime_ms__lte=elapsed,
        ).delete()
        clock.generation = generation
        clock.checkpoint_monotonic_ns = now_ns
        clock.save(update_fields=["generation", "checkpoint_monotonic_ns"])
    return generation


def _expected_generation():
    return os.environ.get("PUREVAULT_CLOCK_GENERATION", "")


def advance_runtime_clock(*, expected_generation=None, timekeeper=False, _monotonic_ns_provider=None):
    expected = _expected_generation() if expected_generation is None else expected_generation
    monotonic_provider = _monotonic_ns_provider or time.monotonic_ns
    if not expected:
        raise ClockUnavailable("The application clock generation is unavailable.")
    for _ in range(100):
        try:
            clock = RuntimeClock.objects.get(pk=1)
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.005)
            continue
        if not clock.generation or clock.generation != expected:
            raise ClockUnavailable("The application clock generation changed.")
        now_ns = monotonic_provider()
        delta_ns = now_ns - clock.checkpoint_monotonic_ns
        delta_ms = delta_ns // 1_000_000
        if 0 <= delta_ms < 1:
            return ClockSnapshot(clock.elapsed_milliseconds)
        if delta_ms < 0:
            with transaction.atomic():
                UnlockGrant.objects.filter(
                    access_until_runtime_ms__isnull=False,
                    eligible_runtime_ms__lte=clock.elapsed_milliseconds,
                ).delete()
            return ClockSnapshot(clock.elapsed_milliseconds, continuity_lost=True)
        lost = delta_ms > MAX_TRUSTED_CLOCK_GAP_MS
        credited_ms = 0 if lost else int(delta_ms)
        next_checkpoint = now_ns if lost else clock.checkpoint_monotonic_ns + credited_ms * 1_000_000
        try:
            with transaction.atomic():
                updated = RuntimeClock.objects.filter(
                    pk=1,
                    generation=clock.generation,
                    checkpoint_monotonic_ns=clock.checkpoint_monotonic_ns,
                    elapsed_milliseconds=clock.elapsed_milliseconds,
                ).update(
                    checkpoint_monotonic_ns=next_checkpoint,
                    elapsed_milliseconds=F("elapsed_milliseconds") + credited_ms,
                )
                if not updated:
                    continue
                elapsed = clock.elapsed_milliseconds + credited_ms
                if lost:
                    UnlockGrant.objects.filter(
                        access_until_runtime_ms__isnull=False,
                        eligible_runtime_ms__lte=elapsed,
                    ).delete()
                return ClockSnapshot(elapsed, continuity_lost=lost)
        except OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.005)
            continue
    raise OperationalError("Unable to checkpoint the Purevault runtime clock after concurrent updates.")


def grant_status(entry, runtime_ms=None):
    runtime_ms = advance_runtime_clock().elapsed_ms if runtime_ms is None else runtime_ms
    try:
        grant = entry.unlock_grant
    except UnlockGrant.DoesNotExist:
        return "locked", None, None
    if grant.eligible_runtime_ms is None:
        return "locked", grant, None
    if runtime_ms < grant.eligible_runtime_ms:
        return "waiting", grant, grant.eligible_runtime_ms - runtime_ms
    if grant.access_until_runtime_ms is not None:
        if runtime_ms >= grant.access_until_runtime_ms:
            return "locked", grant, 0
        return "available", grant, grant.access_until_runtime_ms - runtime_ms
    return "available", grant, None


def grant_state(entry, runtime_ms=None):
    state, grant, _ = grant_status(entry, runtime_ms)
    return state, grant


def start_unlock(entry, reason=None):
    for attempt in range(20):
        try:
            return _start_unlock_once(entry, reason)
        except (IntegrityError, OperationalError) as exc:
            if isinstance(exc, OperationalError) and "locked" not in str(exc).lower():
                raise
            if attempt == 19:
                raise
            time.sleep(0.025)
    raise OperationalError("Unable to start unlock after concurrent updates.")


def _start_unlock_once(entry, reason=None):
    with transaction.atomic():
        runtime_ms = advance_runtime_clock().elapsed_ms
        entry = VaultEntry.objects.select_for_update().get(pk=entry.pk)
        existing = UnlockGrant.objects.filter(entry=entry).first()
        if existing:
            state, _, _ = grant_status(entry, runtime_ms)
            if state != "locked":
                return state, existing, False
            existing.delete()
        partners = list(AccountabilityPartner.objects.filter(owner=entry.owner))
        reason = clean_reason(reason, required=bool(partners)) if partners else ""
        recent_events = MailDelivery.objects.filter(
            owner=entry.owner,
            event_type=MailDelivery.EventType.UNLOCK_REQUESTED,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).values("event_id").distinct().count()
        if partners and recent_events >= 20:
            raise MailRateLimited("Too many accountability email events were requested. Try again later.")
        eligible_runtime_ms = runtime_ms + entry.unlock_delay_seconds * 1000
        access_until_runtime_ms = eligible_runtime_ms + entry.relock_after_seconds * 1000 if entry.relock_policy == VaultEntry.RelockPolicy.AUTOMATIC else None
        now = timezone.now()
        eligible_estimate = now + timedelta(seconds=entry.unlock_delay_seconds)
        access_estimate = eligible_estimate + timedelta(seconds=entry.relock_after_seconds) if entry.relock_policy == VaultEntry.RelockPolicy.AUTOMATIC else None
        grant = UnlockGrant.objects.create(
            entry=entry, requested_at=now, eligible_at=eligible_estimate, access_until=access_estimate,
            requested_runtime_ms=runtime_ms, eligible_runtime_ms=eligible_runtime_ms,
            access_until_runtime_ms=access_until_runtime_ms,
        )
        if partners:
            event_id = uuid.uuid4()
            subject, body = unlock_email(entry.owner, entry, reason, now, eligible_estimate)
            for partner in partners:
                queue_delivery(
                    owner=entry.owner,
                    event_id=event_id,
                    event_type=MailDelivery.EventType.UNLOCK_REQUESTED,
                    recipient_name=partner.name,
                    recipient_email=partner.email,
                    subject=subject,
                    body=body,
                    now=now,
                )
        return "waiting", grant, True


def cancel_or_relock(entry):
    with transaction.atomic():
        advance_runtime_clock()
        return bool(UnlockGrant.objects.filter(entry_id=entry.pk).delete()[0])


def release_secret(entry):
    with transaction.atomic():
        runtime_ms = advance_runtime_clock().elapsed_ms
        entry = VaultEntry.objects.get(pk=entry.pk)
        grant = UnlockGrant.objects.filter(entry=entry).first()
        if not grant:
            return None, "locked"
        state, _, _ = grant_status(entry, runtime_ms)
        if state != "available":
            if state == "locked" and grant.access_until_runtime_ms is not None:
                UnlockGrant.objects.filter(pk=grant.pk).delete()
            return None, state
        now = timezone.now()
        updated = UnlockGrant.objects.filter(pk=grant.pk).update(release_count=F("release_count") + 1, last_released_at=now)
        if not updated:
            return None, "locked"
        return decrypt_secret(entry.encrypted_secret), "available"


def update_entry_if_available(entry, owner, *, encrypted_secret=None, values, relock=False):
    with transaction.atomic():
        runtime_ms = advance_runtime_clock().elapsed_ms
        entry = VaultEntry.objects.select_for_update().get(pk=entry.pk, owner=owner)
        UnlockGrant.objects.select_for_update().filter(entry=entry).first()
        state, _, _ = grant_status(entry, runtime_ms)
        if state != "available":
            raise EntryNotAvailable
        updates = {"updated_at": timezone.now(), **values}
        if encrypted_secret is not None:
            updates["encrypted_secret"] = encrypted_secret
        VaultEntry.objects.filter(pk=entry.pk, owner=owner).update(**updates)
        if relock:
            UnlockGrant.objects.filter(entry=entry).delete()
        return VaultEntry.objects.get(pk=entry.pk)
