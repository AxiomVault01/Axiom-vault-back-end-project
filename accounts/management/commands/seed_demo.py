import os

from django.core.management.base import BaseCommand

from accounts.demo import ADMIN_EMAIL, DEFAULT_PASSWORD, DemoExists, demo_counts, seed


class Command(BaseCommand):
    help = "Creates the demo organisation with a team, uploads, and alerts for the frontend. Safe to run twice."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the existing demo data first, then seed again.")
        parser.add_argument("--password", help="Password for every demo account (default: DEMO_PASSWORD, else the built-in one).")

    def handle(self, *args, reset=False, password=None, **options):
        password = password or os.environ.get("DEMO_PASSWORD") or DEFAULT_PASSWORD
        try:
            result = seed(password=password, reset=reset)
        except DemoExists:
            self.stdout.write("Demo data already exists; nothing changed. Use --reset to rebuild it.")
            return
        self.stdout.write(self.style.SUCCESS(f"Demo organisation '{result['organisation'].name}' created."))
        for name, count in demo_counts().items():
            self.stdout.write(f"  {name}: {count}")
        self.stdout.write(f"Log in as {ADMIN_EMAIL} (role super_admin). Run `manage.py clear_demo --yes` before launch.")
