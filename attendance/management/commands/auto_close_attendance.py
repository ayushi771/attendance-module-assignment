
from django.core.management.base import BaseCommand

from attendance.services import auto_close_stale_attendance


class Command(BaseCommand):
    help = "Auto-close stale attendance records after a specified number of days"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=5,
            help="Number of days after which an open attendance is auto-closed",
        )

    def handle(self, *args, **options):
        days = options["days"]

        count = auto_close_stale_attendance(days_threshold=days)

        self.stdout.write(
            self.style.SUCCESS(
                f"Auto-closed {count} stale attendance record(s) "
                f"after {days} day(s)."
            )
        )

