from django.core.management.base import BaseCommand

from vault.services import initialize_runtime_clock


class Command(BaseCommand):
    help = "Initialize a new Purevault application clock generation."

    def handle(self, *args, **options):
        self.stdout.write(initialize_runtime_clock())

