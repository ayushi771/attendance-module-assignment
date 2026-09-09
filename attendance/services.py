"""
Core attendance calculation logic.

Design principle: PunchLog is immutable and is the source of truth.
Attendance rows are always *derived* from PunchLog + Shift, never
edited by hand -- so if a business rule changes, historical attendance
can be safely recalculated.
"""
from datetime import datetime, timedelta

from django.db import transaction, IntegrityError
from .models import Employee, Shift, PunchLog, Attendance


class AttendanceError(Exception):
    """Raised for business-rule violations (e.g. double punch-in)."""
    pass


# --- Configurable thresholds -------------------------------------------
LATE_GRACE_MINUTES = 10
EARLY_GRACE_MINUTES = 10
EARLY_REJECT_MINUTES = 120
MISSING_PUNCHOUT_GRACE_MINUTES = 30
# -------------------------------------------------------------------------


def get_shift_date_for_timestamp(shift: Shift, ts: datetime):
    """
    Determine which "shift day" a punch belongs to.

    Non-crossing shift (GS): shift day = calendar date of the punch.

    Crossing-midnight shift (NS): the shift day is anchored to when the
    shift STARTS. A punch at or after start_time belongs to that
    calendar date's shift. A punch before start_time (e.g. 2:00 AM)
    belongs to the PREVIOUS calendar date's shift.
    """
    if not shift.crosses_midnight:
        return ts.date()

    if ts.time() >= shift.start_time:
        return ts.date()
    return ts.date() - timedelta(days=1)


def get_shift_midpoint(shift: Shift, shift_date):
    """Real datetime marking the midpoint of the shift, anchored to shift_date."""
    shift_start_dt = datetime.combine(shift_date, shift.start_time)
    return shift_start_dt + timedelta(minutes=shift.half_day_minutes)


def get_shift_end_dt(shift: Shift, shift_date):
    """Real datetime marking when the shift is scheduled to end."""
    shift_start_dt = datetime.combine(shift_date, shift.start_time)
    return shift_start_dt + timedelta(minutes=shift.full_day_minutes)


def compute_halves(shift: Shift, shift_date, punch_in_dt: datetime, punch_out_dt: datetime):
    total_minutes = int((punch_out_dt - punch_in_dt).total_seconds() // 60)

    if total_minutes >= shift.full_day_minutes:
        return "PR", "PR", total_minutes

    if total_minutes >= shift.half_day_minutes:
        midpoint_dt = get_shift_midpoint(shift, shift_date)
        if punch_in_dt < midpoint_dt:
            return "PR", "AB", total_minutes
        else:
            return "AB", "PR", total_minutes

    return "AB", "AB", total_minutes


@transaction.atomic
def punch_in(employee: Employee, ts: datetime = None) -> Attendance:
    ts = ts or datetime.now().replace(microsecond=0)
    shift = employee.shift
    shift_date = get_shift_date_for_timestamp(shift, ts)
    shift_start_dt = datetime.combine(shift_date, shift.start_time)

    minutes_diff = int((ts - shift_start_dt).total_seconds() // 60)

    # Only reject punches WILDLY before shift start (likely a mistake).
    # Ordinary lateness is ALWAYS allowed -- it's just recorded, never blocked.
    if minutes_diff < -EARLY_REJECT_MINUTES:
        raise AttendanceError(
            f"Punch-in at {ts} is {abs(minutes_diff)} minutes before the {shift.code} "
            f"shift starts at {shift_start_dt}. This is too early to be valid -- "
            f"check the shift assignment or punch time."
        )

    existing = Attendance.objects.select_for_update().filter(
        employee=employee, attendance_date=shift_date
    ).first()

    if existing and existing.status == "IN":
        raise AttendanceError(
            f"Employee {employee.employee_code} already punched in for {shift_date} "
            f"at {existing.punch_in_timestamp}. Punch out first."
        )
    if existing and existing.status == "COMPLETED":
        raise AttendanceError(
            f"Employee {employee.employee_code} already has a completed record for {shift_date}."
        )

    is_late = minutes_diff > LATE_GRACE_MINUTES
    late_minutes = (minutes_diff - LATE_GRACE_MINUTES) if is_late else None

    is_early = minutes_diff < -EARLY_GRACE_MINUTES
    early_minutes = abs(minutes_diff) if is_early else None

    try:
        with transaction.atomic():
            PunchLog.objects.create(employee=employee, punch_type="IN", punch_timestamp=ts)
            attendance = existing or Attendance(employee=employee, attendance_date=shift_date, shift=shift)
            attendance.punch_in_timestamp = ts
            attendance.status = "IN"
            attendance.is_late = is_late
            attendance.late_minutes = late_minutes
            attendance.is_early = is_early
            attendance.early_minutes = early_minutes
            attendance.save()
    except IntegrityError:
        raise AttendanceError(
            f"Employee {employee.employee_code} was just punched in by another request "
            f"for {shift_date}. Please refresh and try again."
        )

    return attendance


@transaction.atomic
def punch_out(employee: Employee, ts: datetime = None) -> Attendance:
    ts = ts or datetime.now().replace(microsecond=0)
    shift = employee.shift
    shift_date = get_shift_date_for_timestamp(shift, ts)

    possible_dates = [shift_date]
    if shift.crosses_midnight:
        possible_dates.append(shift_date - timedelta(days=1))

    attendance = (
        Attendance.objects
        .select_for_update()
        .filter(employee=employee, attendance_date__in=possible_dates, status="IN")
        .order_by("-attendance_date")
        .first()
    )
    if not attendance:
        raise AttendanceError(
            f"No open punch-in found for employee {employee.employee_code} around {ts}."
        )
    if ts <= attendance.punch_in_timestamp:
        raise AttendanceError("Punch-out time must be after punch-in time.")

    PunchLog.objects.create(employee=employee, punch_type="OUT", punch_timestamp=ts)

    first_half, second_half, total_minutes = compute_halves(
        shift, attendance.attendance_date, attendance.punch_in_timestamp, ts
    )

    attendance.punch_out_timestamp = ts
    attendance.first_half = first_half
    attendance.second_half = second_half
    attendance.total_worked_minutes = total_minutes
    attendance.status = "COMPLETED"
    attendance.save()
    return attendance


def auto_close_stale_attendance(days_threshold: int = 5) -> int:
    cutoff = datetime.now() - timedelta(days=days_threshold)
    stale_records = Attendance.objects.filter(status="IN", punch_in_timestamp__lte=cutoff)

    count = 0
    for record in stale_records:
        record.first_half = "PT"
        record.second_half = "PT"
        record.status = "AUTO_CLOSED"
        record.flag_note = (
            f"Auto-closed: punched in {record.punch_in_timestamp} but never "
            f"punched out within {days_threshold} days. Needs HR review."
        )
        record.save()
        count += 1

    return count


def check_missing_punch_outs(grace_minutes: int = MISSING_PUNCHOUT_GRACE_MINUTES) -> list:
    newly_flagged = []
    open_records = Attendance.objects.filter(status="IN", missing_punchout_flagged=False)

    for record in open_records:
        shift_end_dt = get_shift_end_dt(record.shift, record.attendance_date)
        deadline = shift_end_dt + timedelta(minutes=grace_minutes)

        if datetime.now() >= deadline:
            record.missing_punchout_flagged = True
            record.flag_note = (
                f"Missing punch-out: shift ended {shift_end_dt.strftime('%I:%M %p')}, "
                f"still punched in as of {datetime.now().strftime('%I:%M %p')}."
            )
            record.save()
            newly_flagged.append(record)

    return newly_flagged