
import random
import time as time_module
from datetime import datetime, timedelta, time

from django.core.management.base import BaseCommand
from attendance.models import Shift, Employee, Attendance
from attendance import services


class Command(BaseCommand):
    help = "Simulates realistic employee punch sessions within their actual shift hours."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=4, help="Seconds between simulated events.")
        parser.add_argument("--employees", type=int, default=10, help="Number of demo employees to simulate.")

    def handle(self, *args, **options):
        interval = options["interval"]
        num_employees = options["employees"]

        # Use the REAL shift definitions -- do not widen them. Punches
        # will be generated to actually fall inside these hours.
        gs, _ = Shift.objects.get_or_create(
            code="GS", defaults=dict(
                name="General Shift", start_time=time(12, 0, 0), end_time=time(21, 0, 0),
                crosses_midnight=False, full_day_minutes=540, half_day_minutes=270,
            )
        )
        ns, _ = Shift.objects.get_or_create(
            code="NS", defaults=dict(
                name="Night Shift", start_time=time(21, 30, 0), end_time=time(6, 30, 0),
                crosses_midnight=True, full_day_minutes=540, half_day_minutes=270,
            )
        )

        first_names = ["Aarav", "Diya", "Kabir", "Isha", "Vihaan", "Meera", "Rohan", "Tara", "Arjun", "Sanya",
                       "Neha", "Karan", "Priya", "Aditya", "Anaya", "Dev", "Ishaan", "Kiara", "Reyansh", "Zara"]
        last_names = ["Sharma", "Patel", "Reddy", "Iyer", "Gupta", "Nair", "Singh", "Das", "Rao", "Kapoor"]

        employees = []
        for i in range(num_employees):
            code = f"{200001 + i}"
            shift = gs if i % 2 == 0 else ns   # alternate GS/NS so you see both
            full_name = f"{first_names[i % len(first_names)]} {last_names[(i // len(first_names)) % len(last_names)]}"
            emp, created = Employee.objects.get_or_create(
                employee_code=code, defaults={"name": full_name, "shift": shift}
            )
            if not created:
                emp.shift = shift
                emp.save()
            employees.append(emp)

        # Each entry: {employee_code: (employee, punch_out_ts)} waiting to
        # be "punched out" on a later tick, so the live feed visibly shows
        # IN now / OUT a few seconds later on screen -- even though the
        # STORED timestamps reflect a realistic shift gap.
        pending = {}

        SCENARIOS = [
            ("full_day", 0.45),      # both halves PR
            ("first_half", 0.15),    # PR / AB
            ("second_half", 0.15),   # AB / PR
            ("absent", 0.15),        # AB / AB
            ("still_in", 0.10),      # no punch-out yet
        ]

        def pick_scenario():
            r = random.random()
            cum = 0
            for name, weight in SCENARIOS:
                cum += weight
                if r <= cum:
                    return name
            return "full_day"

        def shift_start_dt(shift, today):
            return datetime.combine(today, shift.start_time)

        def build_punch_pair(emp):
            """Returns (punch_in_ts, punch_out_ts_or_None) -- real, valid
            timestamps that fall correctly within this employee's shift."""
            shift = emp.shift
            today = datetime.now().date()
            start = shift_start_dt(shift, today)
            scenario = pick_scenario()

            if scenario == "full_day":
                punch_in = start + timedelta(minutes=random.randint(0, 15))
                punch_out = punch_in + timedelta(minutes=shift.full_day_minutes + random.randint(0, 20))
            elif scenario == "first_half":
                punch_in = start + timedelta(minutes=random.randint(0, 15))
                punch_out = punch_in + timedelta(minutes=random.randint(shift.half_day_minutes, shift.half_day_minutes + 40))
            elif scenario == "second_half":
                punch_in = start + timedelta(minutes=shift.half_day_minutes + random.randint(5, 20))
                punch_out = punch_in + timedelta(minutes=random.randint(shift.half_day_minutes, shift.half_day_minutes + 30))
            elif scenario == "absent":
                punch_in = start + timedelta(minutes=random.randint(0, 30))
                punch_out = punch_in + timedelta(minutes=random.randint(20, 100))
            else:  # still_in
                punch_in = start + timedelta(minutes=random.randint(0, 60))
                punch_out = None

            return punch_in, punch_out

        self.stdout.write(self.style.SUCCESS(
            f"Simulating {num_employees} employees with realistic shift-based sessions. Press Ctrl+C to stop."
        ))

        try:
            while True:
                if pending and (not employees or random.random() < 0.5):
                    code = random.choice(list(pending.keys()))
                    emp, punch_out_ts = pending.pop(code)
                    try:
                        a = services.punch_out(emp, punch_out_ts)
                        self.stdout.write(
                            f"[live] {emp.name} ({emp.employee_code}) OUT -> "
                            f"{a.first_half}/{a.second_half}, {a.total_worked_minutes} min"
                        )
                    except services.AttendanceError as e:
                        self.stdout.write(self.style.WARNING(str(e)))
                else:
                    available = [e for e in employees if e.employee_code not in pending]
                    if not available:
                        time_module.sleep(interval)
                        continue
                    emp = random.choice(available)

                    already_done = Attendance.objects.filter(
                        employee=emp, attendance_date=datetime.now().date(), status="COMPLETED"
                    ).exists()
                    if already_done:
                        time_module.sleep(1)
                        continue

                    punch_in_ts, punch_out_ts = build_punch_pair(emp)
                    try:
                        services.punch_in(emp, punch_in_ts)
                        self.stdout.write(f"[live] {emp.name} ({emp.employee_code}) IN at {punch_in_ts.strftime('%H:%M')}")
                        if punch_out_ts:
                            pending[emp.employee_code] = (emp, punch_out_ts)
                    except services.AttendanceError as e:
                        self.stdout.write(self.style.WARNING(str(e)))

                time_module.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.SUCCESS("\nDemo stopped."))