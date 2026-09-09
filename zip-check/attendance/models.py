from django.db import models

class Shift(models.Model):
    code = models.CharField(max_length=10, unique=True) #shiftcode
    name = models.CharField(max_length=100)       #shiftname
    start_time = models.TimeField()                 #shiftstarttime
    end_time = models.TimeField()                   #shiftendtime
    crosses_midnight = models.BooleanField(default=False) #shiftcrossmidnight
    full_day_minutes = models.IntegerField(default=540)
    half_day_minutes = models.IntegerField(default=270)

    def __str__(self):
        return f"{self.code} - {self.name}"

class Employee(models.Model):
    employee_code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=100)
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="employees")

    def __str__(self):
        return f"{self.employee_code} - {self.name}"


class PunchLog(models.Model):
    PUNCH_TYPE_CHOICES = [("IN", "IN"), ("OUT", "OUT")]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="punch_logs")
    punch_type = models.CharField(max_length=3, choices=PUNCH_TYPE_CHOICES)
    punch_timestamp = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["employee", "punch_timestamp"]),
        ]

    def __str__(self):
        return f"{self.employee.employee_code} {self.punch_type} @ {self.punch_timestamp}"

class Attendance(models.Model):
    """
    One row per (employee, attendance_date). Always computed/derived
    by services.py -- never edited directly.
    """
    HALF_STATUS_CHOICES = [("PR", "Present"), ("AB", "Absent"), ("PT", "PT")]
    STATUS_CHOICES = [("IN", "In Progress"), ("COMPLETED", "Completed"), ("AUTO_CLOSED", "Auto Closed")]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendances")
    attendance_date = models.DateField()
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT)

    punch_in_timestamp = models.DateTimeField(null=True, blank=True)
    punch_out_timestamp = models.DateTimeField(null=True, blank=True)

    first_half = models.CharField(max_length=2, choices=HALF_STATUS_CHOICES, null=True, blank=True)
    second_half = models.CharField(max_length=2, choices=HALF_STATUS_CHOICES, null=True, blank=True)
    total_worked_minutes = models.IntegerField(null=True, blank=True)

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="IN")

    is_late = models.BooleanField(default=False)
    late_minutes = models.IntegerField(null=True, blank=True)
    is_early = models.BooleanField(default=False)
    early_minutes = models.IntegerField(null=True, blank=True)

    missing_punchout_flagged = models.BooleanField(default=False)
    flag_note = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "attendance_date"], name="uq_employee_attendance_date")
        ]
        indexes = [
            models.Index(fields=["employee", "attendance_date"]),
            models.Index(fields=["attendance_date"]),
        ]

    def __str__(self):
        return f"{self.employee.employee_code} - {self.attendance_date} - {self.status}"