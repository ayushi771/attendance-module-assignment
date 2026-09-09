
"""
Concurrency tests using real threads to prove the race-condition
protections in services.py actually work under simultaneous load.

Uses TransactionTestCase (not TestCase) because normal TestCase wraps
each test in a transaction that isn't visible to other threads, which
would make genuine concurrency impossible to test.
"""

import threading
from datetime import time, datetime

from django.test import TransactionTestCase
from django.db import close_old_connections

from .models import Shift, Employee, Attendance
from . import services


def dt(s):
    """Convert DD-MM-YYYY HH:MM:SS string into a datetime object."""
    return datetime.strptime(s, "%d-%m-%Y %H:%M:%S")


class ConcurrentPunchTests(TransactionTestCase):

    def setUp(self):
        self.gs = Shift.objects.create(
            code="GS",
            name="General Shift",
            start_time=time(12, 0),
            end_time=time(21, 0),
            crosses_midnight=False,
            full_day_minutes=540,
            half_day_minutes=270,
        )

    def test_multiple_different_employees_punch_in_simultaneously(self):
        """
        5 DIFFERENT employees punching in at the same instant should
        all succeed independently.

        Row-level locking is per employee/attendance record, so
        different employees should not conflict with each other.
        """

        employees = [
            Employee.objects.create(
                employee_code=f"CONC{i}",
                name=f"Employee{i}",
                shift=self.gs,
            )
            for i in range(5)
        ]

        results = {}
        errors = {}

        # Release all 5 threads together to simulate simultaneous requests.
        barrier = threading.Barrier(5)

        def do_punch_in(emp, idx):
            # Make sure this worker starts with a clean DB connection.
            close_old_connections()

            try:
                barrier.wait()

                attendance = services.punch_in(
                    emp,
                    dt("05-09-2026 12:10:00"),
                )

                results[idx] = attendance

            except services.AttendanceError as e:
                errors[idx] = str(e)

            finally:
                # Very important: close the connection created/used
                # by this worker thread so PostgreSQL can clean up
                # the temporary test database after the test finishes.
                close_old_connections()

        threads = [
            threading.Thread(
                target=do_punch_in,
                args=(employees[i], i),
            )
            for i in range(5)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # All 5 should succeed because they are different employees.
        self.assertEqual(
            len(results),
            5,
            f"Expected all 5 to succeed, errors: {errors}",
        )

        self.assertEqual(len(errors), 0)

        # Verify each employee has an open attendance record.
        for i in range(5):
            record = Attendance.objects.get(
                employee=employees[i]
            )

            self.assertEqual(record.status, "IN")

    def test_same_employee_double_punch_in_race_condition(self):
        """
        The SAME employee attempts to punch in through 5
        near-simultaneous requests.

        This simulates situations such as:
        - double-click
        - request retry
        - multiple devices
        - simultaneous API requests

        Exactly ONE request should succeed.

        The remaining four should be rejected cleanly with
        AttendanceError.

        There must never be duplicate Attendance records.
        """

        emp = Employee.objects.create(
            employee_code="RACE1",
            name="RaceTest",
            shift=self.gs,
        )

        results = []
        errors = []

        # Protect shared Python lists from concurrent writes.
        lock = threading.Lock()

        # Release all 5 requests together.
        barrier = threading.Barrier(5)

        def do_punch_in():
            close_old_connections()

            try:
                barrier.wait()

                attendance = services.punch_in(
                    emp,
                    dt("05-09-2026 12:10:00"),
                )

                with lock:
                    results.append(attendance)

            except services.AttendanceError as e:
                with lock:
                    errors.append(str(e))

            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=do_punch_in)
            for _ in range(5)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Exactly one request should succeed.
        self.assertEqual(
            len(results),
            1,
            f"Expected exactly 1 success, got {len(results)}",
        )

        # The other four requests should be rejected.
        self.assertEqual(
            len(errors),
            4,
            f"Expected exactly 4 rejections, got {len(errors)}",
        )

        # Most importantly, there must be only ONE Attendance row
        # for this employee and attendance date.
        count = Attendance.objects.filter(
            employee=emp,
            attendance_date="2026-09-05",
        ).count()

        self.assertEqual(count, 1)

    def test_same_employee_double_punch_out_race_condition(self):
        """
        Same concurrency test as above, but for punch-out.

        First create one legitimate punch-in.

        Then send 5 simultaneous punch-out requests.

        Exactly ONE should succeed and the remaining four should
        be rejected.
        """

        emp = Employee.objects.create(
            employee_code="RACE2",
            name="RaceOutTest",
            shift=self.gs,
        )

        # Create the legitimate punch-in first.
        services.punch_in(
            emp,
            dt("05-09-2026 12:10:00"),
        )

        results = []
        errors = []

        lock = threading.Lock()
        barrier = threading.Barrier(5)

        def do_punch_out():
            close_old_connections()

            try:
                barrier.wait()

                attendance = services.punch_out(
                    emp,
                    dt("05-09-2026 21:10:00"),
                )

                with lock:
                    results.append(attendance)

            except services.AttendanceError as e:
                with lock:
                    errors.append(str(e))

            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=do_punch_out)
            for _ in range(5)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Exactly one punch-out should succeed.
        self.assertEqual(
            len(results),
            1,
            f"Expected exactly 1 success, got {len(results)}",
        )

        # The remaining four should be rejected.
        self.assertEqual(
            len(errors),
            4,
            f"Expected exactly 4 rejections, got {len(errors)}",
        )

        # Verify the final attendance state.
        record = Attendance.objects.get(employee=emp)

        self.assertEqual(
            record.status,
            "COMPLETED",
        )

        self.assertEqual(
            record.total_worked_minutes,
            540,
        )

