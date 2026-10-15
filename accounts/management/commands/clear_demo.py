from django.core.management.base import BaseCommand

from accounts.demo import clear, demo_counts


class Command(BaseCommand):
    help = "Deletes every row of the demo organisation(s), and nothing else. Run before launch."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Really delete. Without it, only report what would go.")

    def handle(self, *args, yes=False, **options):
        counts = demo_counts()
        if not counts["organisations"]:
            self.stdout.write("No demo data found; nothing to delete.")
            return
        for name, count in counts.items():
            self.stdout.write(f"  {name}: {count}")
        if not yes:
            self.stdout.write("Nothing deleted. Run again with --yes to delete the demo data above.")
            return
        clear()
        self.stdout.write(self.style.SUCCESS("Demo data deleted."))
