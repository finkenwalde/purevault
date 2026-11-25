import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from vault.mail_service import claim_due_delivery, process_claimed_delivery, prune_accepted_history


logger = logging.getLogger("vault.mail_dispatcher")


class Command(BaseCommand):
    help = "Dispatch queued Purevault accountability emails."

    def handle(self, *args, **options):
        last_prune = 0.0
        while True:
            try:
                close_old_connections()
                now = time.monotonic()
                if now - last_prune >= 3600:
                    prune_accepted_history()
                    last_prune = now
                delivery, token = claim_due_delivery()
                if delivery:
                    process_claimed_delivery(delivery, token)
                    continue
            except Exception:
                logger.exception("The mail dispatcher recovered from an internal error without exposing message contents or credentials.")
            time.sleep(1)
