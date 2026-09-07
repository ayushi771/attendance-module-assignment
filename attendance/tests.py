from datetime import datetime, time, timedelta

from django.test import TestCase
from .models import Shift, Employee, Attendance
from . import services

def dt(s):
    return datetime.strptime(s, "%d-%m-%Y %H:%M:%S")

class AttendanceLogicTests(TestCase):
    def setUp(self):
        self.gs = Shift.objects.create(
            code="GS", name="General Shift", start_time=time(12, 0), end_time=time(21, 0),
            crosses_midnight=False, full_day_minutes=540, half_day_minutes=270,
        )
        self.ns = Shift.objects.create(
            code="NS", name="Night Shift", start_time=time(21, 30), end_time=time(6, 30),
            crosses_midnight=True, full_day_minutes=540, half_day_minutes=270,
        )

    def test_full_day_gs(self):
        emp = Employee.objects.create(employee_code="E1", name="Romin", shift=self.gs)
        services.punch_in(emp, dt("05-09-2026 12:10:00"))
        a = services.punch_out(emp, dt("05-09-2026 21:10:00"))

        self.assertEqual(a.first_half, "PR")
        self.assertEqual(a.second_half, "PR")
        self.assertEqual(a.total_worked_minutes, 540)
        self.assertEqual(a.status, "COMPLETED")

    def test_full_day_night_shift_crosses_midnight(self):
        emp = Employee.objects.create(employee_code="E2", name="Deepesh", shift=self.ns)
        services.punch_in(emp, dt("05-09-2026 21:30:00"))
        a = services.punch_out(emp, dt("06-09-2026 06:30:00"))

        self.assertEqual(str(a.attendance_date), "2026-09-05")
        self.assertEqual(a.first_half, "PR")
        self.assertEqual(a.second_half, "PR")
        self.assertEqual(a.total_worked_minutes, 540)

    def test_partial_day_first_half_only(self):
        """worked 4h40m, started before midpoint -> First Half PR only."""
        emp = Employee.objects.create(employee_code="E3", name="Romin", shift=self.gs)
        services.punch_in(emp, dt("06-09-2026 12:50:00"))
        a = services.punch_out(emp, dt("06-09-2026 17:30:00"))

        self.assertEqual(a.first_half, "PR")
        self.assertEqual(a.second_half, "AB")
        self.assertEqual(a.total_worked_minutes, 280)

    def test_short_shift_both_absent(self):
        """only 2h worked -> both halves AB."""
        emp = Employee.objects.create(employee_code="E4", name="Deepesh", shift=self.ns)
        services.punch_in(emp, dt("06-09-2026 21:30:00"))
        a = services.punch_out(emp, dt("06-09-2026 23:30:00"))

        self.assertEqual(a.first_half, "AB")
        self.assertEqual(a.second_half, "AB")
        self.assertEqual(a.total_worked_minutes, 120)

    def test_punch_in_only_status_is_in_progress(self):
        """punched in, no punch-out yet -> status IN, halves blank."""
        emp = Employee.objects.create(employee_code="E5", name="Romin", shift=self.gs)
        a = services.punch_in(emp, dt("07-09-2026 12:15:00"))

        self.assertEqual(a.status, "IN")
        self.assertIsNone(a.first_half)
        self.assertIsNone(a.second_half)
        self.assertIsNone(a.punch_out_timestamp)

    def test_night_shift_second_half_only(self):
        """
         punches in after the shift's midpoint (on the
        NEXT calendar date), so attendance_date resolves back to the shift's
        start date, and only Second Half is credited.
        """
        emp = Employee.objects.create(employee_code="E6", name="Deepesh", shift=self.ns)

        a_in = services.punch_in(emp, dt("08-09-2026 02:00:00"))
        self.assertEqual(str(a_in.attendance_date), "2026-09-07")

        a = services.punch_out(emp, dt("08-09-2026 06:30:00"))
        self.assertEqual(a.first_half, "AB")
        self.assertEqual(a.second_half, "PR")
        self.assertEqual(a.total_worked_minutes, 270)

    def test_double_punch_in_raises(self):
        """Punching in twice without punching out first should be blocked."""
        emp = Employee.objects.create(employee_code="E7", name="Romin", shift=self.gs)
        services.punch_in(emp, dt("05-09-2026 12:10:00"))

        with self.assertRaises(services.AttendanceError):
            services.punch_in(emp, dt("05-09-2026 14:00:00"))

    def test_punch_out_without_punch_in_raises(self):
        """Punching out with no open punch-in should be blocked."""
        emp = Employee.objects.create(employee_code="E8", name="Romin", shift=self.gs)

        with self.assertRaises(services.AttendanceError):
            services.punch_out(emp, dt("05-09-2026 18:00:00"))

    def test_punch_out_before_punch_in_raises(self):
        """Punch-out timestamp earlier than punch-in should be rejected."""
        emp = Employee.objects.create(employee_code="E9", name="Romin", shift=self.gs)
        services.punch_in(emp, dt("05-09-2026 15:00:00"))

        with self.assertRaises(services.AttendanceError):
            services.punch_out(emp, dt("05-09-2026 14:00:00"))

    def test_second_punch_in_after_completed_day_raises(self):
        """Once a day is COMPLETED, punching in again for the same shift-day is blocked."""
        emp = Employee.objects.create(employee_code="E10", name="Romin", shift=self.gs)
        services.punch_in(emp, dt("05-09-2026 12:10:00"))
        services.punch_out(emp, dt("05-09-2026 21:10:00"))

        with self.assertRaises(services.AttendanceError):
            services.punch_in(emp, dt("05-09-2026 21:30:00"))

    def test_auto_close_after_5_days_marks_pt(self):
        """Stale IN records older than 5 days get auto-closed with PT/PT."""
        emp = Employee.objects.create(employee_code="E11", name="Forgot", shift=self.gs)
        old_time = datetime.now() - timedelta(days=6)
        Attendance.objects.create(
            employee=emp, attendance_date=old_time.date(), shift=self.gs,
            punch_in_timestamp=old_time, status="IN",
        )

        count = services.auto_close_stale_attendance(days_threshold=5)

        self.assertEqual(count, 1)
        a = Attendance.objects.get(employee=emp)
        self.assertEqual(a.first_half, "PT")
        self.assertEqual(a.second_half, "PT")
        self.assertEqual(a.status, "AUTO_CLOSED")

    def test_auto_close_does_not_touch_recent_in_progress(self):
        """A recent (< 5 days old) IN record should NOT be auto-closed."""
        emp = Employee.objects.create(employee_code="E12", name="RecentlyIn", shift=self.gs)
        recent_time = datetime.now() - timedelta(hours=2)
        Attendance.objects.create(
            employee=emp, attendance_date=recent_time.date(), shift=self.gs,
            punch_in_timestamp=recent_time, status="IN",
        )

        count = services.auto_close_stale_attendance(days_threshold=5)

        self.assertEqual(count, 0)
        a = Attendance.objects.get(employee=emp)
        self.assertEqual(a.status, "IN")
    