from django.core.management.base import BaseCommand
from attendance import services


class Command(BaseCommand):
    help = "Checks for employees whose shift ended (plus grace period) but never punched out today."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace", type=int, default=30,
            help="Minutes after shift end before flagging (default: 30)."
        )

    def handle(self, *args, **options):
        grace = options["grace"]
        flagged = services.check_missing_punch_outs(grace_minutes=grace)

        if not flagged:
            self.stdout.write(self.style.SUCCESS("No missing punch-outs detected."))
            return

        for r in flagged:
            shift_end = services.get_shift_end_dt(r.shift, r.attendance_date)
            self.stdout.write(self.style.WARNING(
                "\n"
                "Missing Punch-Out\n"
                "----------------------------\n"
                f"Employee:           {r.employee.name}\n"
                f"Employee ID:        {r.employee.employee_code}\n"
                f"Shift:              {r.shift.name}\n"
                f"Punch In:           {r.punch_in_timestamp.strftime('%I:%M %p')}\n"
                f"Expected Shift End: {shift_end.strftime('%I:%M %p')}\n"
                f"Status:             Still Punched In\n"
            ))

        self.stdout.write(self.style.WARNING(f"\n{len(flagged)} record(s) flagged for review."))