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


    def test_exact_270_minutes_is_half_day_pr(self):
        """Exactly 270 minutes (4.5h) should just barely qualify as half-day PR."""
        emp = Employee.objects.create(employee_code="E13", name="Boundary270", shift=self.gs)
        services.punch_in(emp, dt("05-09-2026 12:00:00"))
        a = services.punch_out(emp, dt("05-09-2026 16:30:00"))  # exactly 270 min
        self.assertEqual(a.first_half, "PR")
        self.assertEqual(a.second_half, "AB")
        self.assertEqual(a.total_worked_minutes, 270)

    def test_exact_540_minutes_is_full_day_pr(self):
        """Exactly 540 minutes (9h) should just barely qualify as full-day PR/PR."""
        emp = Employee.objects.create(employee_code="E14", name="Boundary540", shift=self.gs)
        services.punch_in(emp, dt("05-09-2026 12:00:00"))
        a = services.punch_out(emp, dt("05-09-2026 21:00:00"))  # exactly 540 min
        self.assertEqual(a.first_half, "PR")
        self.assertEqual(a.second_half, "PR")
        self.assertEqual(a.total_worked_minutes, 540)

    def test_gs_punch_out_never_matches_previous_day_stale_record(self):
        """
        A GS punch-out should NEVER close a stale open record from a
        previous day, since GS doesn't cross midnight. This is the fix
        for the punch-out lookback bug.
        """
        emp = Employee.objects.create(employee_code="E15", name="StaleGS", shift=self.gs)

        # Day 1: punch in, forget to punch out (stays open forever)
        services.punch_in(emp, dt("05-09-2026 12:10:00"))

        # Day 2: punch in creates a NEW record (different attendance_date)
        services.punch_in(emp, dt("06-09-2026 12:05:00"))

        # Day 2: punch out should close Day 2's record, NOT Day 1's stale one
        a = services.punch_out(emp, dt("06-09-2026 21:10:00"))
        self.assertEqual(str(a.attendance_date), "2026-09-06")
        self.assertEqual(a.total_worked_minutes, 545)  # ~9h05m, correctly from Day 2 only

        # Day 1's record should still be sitting open, untouched
        day1_record = Attendance.objects.get(employee=emp, attendance_date="2026-09-05")
        self.assertEqual(day1_record.status, "IN")
        self.assertIsNone(day1_record.punch_out_timestamp)

    def test_late_punch_in_beyond_grace_is_flagged(self):
        """Punch-in 27 min after shift start (grace=10) -> late by 17 min, still allowed."""
        emp = Employee.objects.create(employee_code="E16", name="LateGuy", shift=self.gs)
        a = services.punch_in(emp, dt("05-09-2026 12:27:00"))
        self.assertTrue(a.is_late)
        self.assertEqual(a.late_minutes, 17)  # 27 - 10 grace minutes
        self.assertFalse(a.is_early)

    def test_punch_in_within_grace_is_not_late(self):
        """Punch-in 7 min after shift start (grace=10) -> NOT flagged as late."""
        emp = Employee.objects.create(employee_code="E17", name="OnTimeGuy", shift=self.gs)
        a = services.punch_in(emp, dt("05-09-2026 12:07:00"))
        self.assertFalse(a.is_late)
        self.assertIsNone(a.late_minutes)

    def test_early_punch_in_within_reject_window_is_allowed_and_flagged(self):
        """Punch-in 45 min before shift start -> allowed, flagged as early."""
        emp = Employee.objects.create(employee_code="E18", name="EarlyGuy", shift=self.gs)
        a = services.punch_in(emp, dt("05-09-2026 11:15:00"))  # 45 min early
        self.assertTrue(a.is_early)
        self.assertEqual(a.early_minutes, 45)
        self.assertFalse(a.is_late)

    def test_extremely_early_punch_in_is_rejected(self):
        """Punch-in more than 120 min before shift start -> rejected outright."""
        emp = Employee.objects.create(employee_code="E19", name="TooEarlyGuy", shift=self.gs)
        with self.assertRaises(services.AttendanceError):
            services.punch_in(emp, dt("05-09-2026 08:00:00"))  # 240 min early

    def test_missing_punch_out_flagged_after_shift_end_plus_grace(self):
        """An open punch past shift end + grace should be flagged exactly once."""
        emp = Employee.objects.create(employee_code="E20", name="ForgotOut", shift=self.gs)
        # Use a full 2 days in the past (not a fixed hour offset) so this
        # test is deterministic regardless of what time of day it's run --
        # a smaller offset like 15 hours can land ambiguously depending on
        # the current wall-clock time, since the deadline being checked is
        # a specific clock time (shift end + grace) on a specific date.
        past_time = datetime.now() - timedelta(days=2)
        Attendance.objects.create(
            employee=emp, attendance_date=past_time.date(), shift=self.gs,
            punch_in_timestamp=past_time, status="IN",
        )

        flagged = services.check_missing_punch_outs(grace_minutes=30)
        self.assertEqual(len(flagged), 1)
        self.assertTrue(flagged[0].missing_punchout_flagged)
        self.assertIn("Missing punch-out", flagged[0].flag_note)

        # Running it again should NOT re-flag the same record
        flagged_again = services.check_missing_punch_outs(grace_minutes=30)
        self.assertEqual(len(flagged_again), 0)

    def test_recent_open_punch_not_flagged_as_missing(self):
        """An open punch still within the shift window should NOT be flagged."""
        emp = Employee.objects.create(employee_code="E21", name="StillWorking", shift=self.gs)
        recent_time = datetime.now()
        Attendance.objects.create(
            employee=emp, attendance_date=recent_time.date(), shift=self.gs,
            punch_in_timestamp=recent_time, status="IN",
        )

        flagged = services.check_missing_punch_outs(grace_minutes=30)
        self.assertEqual(len(flagged), 0)    

    def test_night_shift_late_punch_in_not_falsely_rejected_as_early(self):
        """
        Regression guard: a night-shift punch-in at 2 AM (well after the
        shift's 21:30 start the previous evening) must be treated as LATE,
        not incorrectly flagged as ~1170 minutes EARLY. This would only
        happen if shift_start_dt were built from the raw punch date
        instead of the resolved shift_date -- this test locks in the
        correct behavior.
        """
        emp = Employee.objects.create(employee_code="E22", name="NightLate", shift=self.ns)
        # Should NOT raise AttendanceError
        a = services.punch_in(emp, dt("08-09-2026 02:00:00"))
        self.assertEqual(str(a.attendance_date), "2026-09-07")
        self.assertTrue(a.is_late)
        self.assertEqual(a.late_minutes, 260)  # 270 min diff - 10 min grace
        self.assertFalse(a.is_early)    

