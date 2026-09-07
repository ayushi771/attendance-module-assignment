from django.contrib import admin
from .models import Shift, Employee, PunchLog, Attendance

@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "start_time", "crosses_midnight", "full_day_minutes")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_code", "name", "shift")

@admin.register(PunchLog)
class PunchLogAdmin(admin.ModelAdmin):
    list_display = ("employee" , "punch_type", "punch_timestamp")
    list_filter = ("punch_type" ,)

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "employee", "attendance_date", "shift", "punch_in_timestamp",
        "punch_out_timestamp", "first_half", "second_half",
        "total_worked_minutes", "status",
    )
    list_filter = ("shift", "status", "attendance_date")    