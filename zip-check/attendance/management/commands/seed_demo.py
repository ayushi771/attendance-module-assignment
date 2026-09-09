"""
Wipes all attendance data and recreates the exact scenario from the
assignment's "Expected Output" table, so you can verify your module
matches it exactly.
"""

from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.db import transaction

from attendance.models import Shift, Employee, Attendance, PunchLog
from attendance import services


def dt(s):
    return datetime.strptime(s, "%d-%m-%Y %H:%M:%S")


class Command(BaseCommand):
    help = "Wipes and re-seeds demo data matching the assignment's exact sample scenario."

    @transaction.atomic
    def handle(self, *args, **options):

        # Clean slate so this is repeatable
        Attendance.objects.all().delete()
        PunchLog.objects.all().delete()
        Employee.objects.all().delete()
        Shift.objects.all().delete()

        gs = Shift.objects.create(
            code="GS",
            name="General Shift",
            start_time=time(12, 0, 0),
            end_time=time(21, 0, 0),
            crosses_midnight=False,
            full_day_minutes=540,
            half_day_minutes=270,
        )

        ns = Shift.objects.create(
            code="NS",
            name="Night Shift",
            start_time=time(21, 30, 0),
            end_time=time(6, 30, 0),
            crosses_midnight=True,
            full_day_minutes=540,
            half_day_minutes=270,
        )

        # Employee names changed only
        rahul = Employee.objects.create(
            employee_code="123456",
            name="Rahul",
            shift=gs
        )

        amit = Employee.objects.create(
            employee_code="101010",
            name="Amit",
            shift=ns
        )

        # --- 05-09-2026: full day for both ---

        services.punch_in(
            rahul,
            dt("05-09-2026 12:10:00")
        )

        services.punch_out(
            rahul,
            dt("05-09-2026 21:10:00")
        )

        services.punch_in(
            amit,
            dt("05-09-2026 21:30:00")
        )

        services.punch_out(
            amit,
            dt("06-09-2026 06:30:00")
        )

        # --- 06-09-2026: partial day for both ---

        services.punch_in(
            rahul,
            dt("06-09-2026 12:50:00")
        )

        services.punch_out(
            rahul,
            dt("06-09-2026 17:30:00")
        )

        services.punch_in(
            amit,
            dt("06-09-2026 21:30:00")
        )

        services.punch_out(
            amit,
            dt("06-09-2026 23:30:00")
        )

        # --- 07-09-2026: Rahul still IN,
        # Amit works only the second half ---

        services.punch_in(
            rahul,
            dt("07-09-2026 12:15:00")
        )

        services.punch_in(
            amit,
            dt("08-09-2026 02:00:00")
        )  # resolves to shift-date 07-09

        services.punch_out(
            amit,
            dt("08-09-2026 06:30:00")
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Seed complete. Refresh the dashboard to view results."
            )
        )