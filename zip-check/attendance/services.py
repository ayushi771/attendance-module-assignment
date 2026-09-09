from datetime import datetime, timedelta
from django.db import transaction, IntegrityError
from .models import Employee, Shift, PunchLog, Attendance


class AttendanceError(Exception):
    pass


LATE_GRACE_MINUTES = 10
EARLY_GRACE_MINUTES = 10
EARLY_REJECT_MINUTES = 0
MISSING_PUNCHOUT_GRACE_MINUTES = 30


def get_shift_date_for_timestamp(shift, ts):
    if not shift.crosses_midnight:
        return ts.date()

    if ts.time() >= shift.start_time:
        return ts.date()

    return ts.date() - timedelta(days=1)


def get_shift_midpoint(shift, shift_date):
    shift_start_dt = datetime.combine(
        shift_date,
        shift.start_time
    )

    return shift_start_dt + timedelta(
        minutes=shift.half_day_minutes
    )


def get_shift_end_dt(shift, shift_date):
    shift_start_dt = datetime.combine(
        shift_date,
        shift.start_time
    )

    return shift_start_dt + timedelta(
        minutes=shift.full_day_minutes
    )


def compute_halves(shift, shift_date, punch_in_dt, punch_out_dt):
    total_minutes = int(
        (punch_out_dt - punch_in_dt).total_seconds() // 60
    )

    if total_minutes >= shift.full_day_minutes:
        return "PR", "PR", total_minutes

    if total_minutes >= shift.half_day_minutes:
        midpoint_dt = get_shift_midpoint(
            shift,
            shift_date
        )

        if punch_in_dt < midpoint_dt:
            return "PR", "AB", total_minutes
        else:
            return "AB", "PR", total_minutes

    return "AB", "AB", total_minutes


def validate_punch_in_time(shift, ts):
    """
    Prevent employees from punching into the wrong shift.

    GS:
        12:00 - 21:00

    NS:
        21:30 - 06:30 next day
    """

    shift_date = get_shift_date_for_timestamp(
        shift,
        ts
    )

    shift_start_dt = datetime.combine(
        shift_date,
        shift.start_time
    )

    shift_end_dt = shift_start_dt + timedelta(
        minutes=shift.full_day_minutes
    )

    # Allow a small early/late tolerance around the actual shift.
    allowed_start = (
        shift_start_dt -
        timedelta(minutes=EARLY_REJECT_MINUTES)
    )

    allowed_end = (
        shift_end_dt +
        timedelta(minutes=EARLY_REJECT_MINUTES)
    )

    if ts < allowed_start:
        raise AttendanceError(
            f"Punch-in is too early for {shift.code} shift. "
            f"Allowed shift: "
            f"{shift_start_dt.strftime('%I:%M %p')} - "
            f"{shift_end_dt.strftime('%I:%M %p')}."
        )

    if ts > allowed_end:
        raise AttendanceError(
            f"Punch-in is outside the {shift.code} shift window. "
            f"Allowed shift: "
            f"{shift_start_dt.strftime('%I:%M %p')} - "
            f"{shift_end_dt.strftime('%I:%M %p')}."
        )

    return shift_date, shift_start_dt, shift_end_dt


@transaction.atomic
def punch_in(employee, ts=None):

    ts = ts or datetime.now().replace(
        microsecond=0
    )

    shift = employee.shift

    # -------------------------------------------------
    # IMPORTANT:
    # Validate that this punch belongs to employee's
    # assigned shift.
    # -------------------------------------------------

    shift_date, shift_start_dt, shift_end_dt = (
        validate_punch_in_time(
            shift,
            ts
        )
    )

    minutes_diff = int(
        (ts - shift_start_dt).total_seconds() // 60
    )

    existing = (
        Attendance.objects
        .select_for_update()
        .filter(
            employee=employee,
            attendance_date=shift_date
        )
        .first()
    )

    if existing and existing.status == "IN":
        raise AttendanceError(
            "Employee is already punched in."
        )

    if existing and existing.status == "COMPLETED":
        raise AttendanceError(
            "Attendance for this shift is already completed."
        )

    is_late = (
        minutes_diff > LATE_GRACE_MINUTES
    )

    late_minutes = (
        minutes_diff - LATE_GRACE_MINUTES
        if is_late
        else None
    )

    is_early = (
        minutes_diff < -EARLY_GRACE_MINUTES
    )

    early_minutes = (
        abs(minutes_diff)
        if is_early
        else None
    )

    try:

        with transaction.atomic():

            PunchLog.objects.create(
                employee=employee,
                punch_type="IN",
                punch_timestamp=ts
            )

            attendance = (
                existing or
                Attendance(
                    employee=employee,
                    attendance_date=shift_date,
                    shift=shift
                )
            )

            attendance.punch_in_timestamp = ts
            attendance.status = "IN"

            attendance.is_late = is_late
            attendance.late_minutes = late_minutes

            attendance.is_early = is_early
            attendance.early_minutes = early_minutes

            attendance.save()

    except IntegrityError:
        raise AttendanceError(
            "Attendance already exists for this employee and date."
        )

    return attendance


@transaction.atomic
def punch_out(employee, ts=None):

    ts = ts or datetime.now().replace(
        microsecond=0
    )

    shift = employee.shift

    shift_date = get_shift_date_for_timestamp(
        shift,
        ts
    )

    possible_dates = [shift_date]

    if shift.crosses_midnight:
        possible_dates.append(
            shift_date - timedelta(days=1)
        )

    attendance = (
        Attendance.objects
        .select_for_update()
        .filter(
            employee=employee,
            attendance_date__in=possible_dates,
            status="IN"
        )
        .order_by("-attendance_date")
        .first()
    )

    if not attendance:
        raise AttendanceError(
            "No active punch-in found."
        )

    if ts <= attendance.punch_in_timestamp:
        raise AttendanceError(
            "Punch-out time must be after punch-in time."
        )

    PunchLog.objects.create(
        employee=employee,
        punch_type="OUT",
        punch_timestamp=ts
    )

    first_half, second_half, total_minutes = (
        compute_halves(
            shift,
            attendance.attendance_date,
            attendance.punch_in_timestamp,
            ts
        )
    )

    attendance.punch_out_timestamp = ts
    attendance.first_half = first_half
    attendance.second_half = second_half
    attendance.total_worked_minutes = total_minutes
    attendance.status = "COMPLETED"

    attendance.save()

    return attendance


def auto_close_stale_attendance(days_threshold=5):

    cutoff = (
        datetime.now() -
        timedelta(days=days_threshold)
    )

    stale_records = (
        Attendance.objects
        .filter(
            status="IN",
            punch_in_timestamp__lte=cutoff
        )
    )

    count = 0

    for record in stale_records:

        record.first_half = "PT"
        record.second_half = "PT"
        record.status = "AUTO_CLOSED"

        record.flag_note = (
            f"Auto-closed: punched in "
            f"{record.punch_in_timestamp} but never "
            f"punched out within {days_threshold} days. "
            f"Needs HR review."
        )

        record.save()

        count += 1

    return count