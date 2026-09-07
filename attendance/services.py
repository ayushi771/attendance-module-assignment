
from datetime import datetime, timedelta

from django.db import transaction, IntegrityError
from .models import Employee, Shift, PunchLog, Attendance


class AttendanceError(Exception):
    """Raised for business-rule violations (e.g. double punch-in)."""
    pass


def get_shift_date_for_timestamp(shift: Shift, ts: datetime):
  
    if not shift.crosses_midnight:
        return ts.date()

    if ts.time() >= shift.start_time:
        return ts.date()
    return ts.date() - timedelta(days=1)


def get_shift_midpoint(shift: Shift, shift_date):
   
    shift_start_dt = datetime.combine(shift_date, shift.start_time)
    return shift_start_dt + timedelta(minutes=shift.half_day_minutes)


def compute_halves(shift: Shift, shift_date, punch_in_dt: datetime, punch_out_dt: datetime):
    """
    - total worked >= full_day_minutes  -> both halves PR
    - total worked >= half_day_minutes  -> ONE half PR: whichever half
      contains the punch-in moment, relative to the shift's midpoint
    - otherwise                         -> both halves AB
    """
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

    try:
        with transaction.atomic():
            PunchLog.objects.create(employee=employee, punch_type="IN", punch_timestamp=ts)
            attendance = existing or Attendance(employee=employee, attendance_date=shift_date, shift=shift)
            attendance.punch_in_timestamp = ts
            attendance.status = "IN"
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
        .filter(
            employee=employee,
            attendance_date__in=possible_dates,
            status="IN",
        )
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
    """
    Finds attendance records still stuck in "IN" status where the
    punch-in happened `days_threshold` or more days ago, and marks
    both halves as "PT" (per business rule). Returns the number of
    records closed.

    This does NOT touch PunchLog -- the original punch-in is preserved.
    Only the derived Attendance summary is updated, consistent with our
    "PunchLog is the immutable source of truth" design.

    """
    cutoff = datetime.now() - timedelta(days=days_threshold)

    stale_records = Attendance.objects.filter(
        status="IN",
        punch_in_timestamp__lte=cutoff,
    )

    count = stale_records.update(
        first_half="PT",
        second_half="PT",
        status="AUTO_CLOSED",
    )
    return count