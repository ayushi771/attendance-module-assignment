from rest_framework import serializers
from .models import Shift, Employee, Attendance
from .models import PunchLog

class PunchLogSerializer(serializers.ModelSerializer):
    employee_code = serializers.CharField(source="employee.employee_code")
    employee_name = serializers.CharField(source="employee.name")

    class Meta:
        model = PunchLog
        fields = ["id", "employee_code", "employee_name", "punch_type", "punch_timestamp"]

class ShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shift
        fields = "__all__"

class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = "__all__"

class PunchRequestSerializer(serializers.Serializer):
    employee_code = serializers.CharField()
    timestamp = serializers.DateTimeField(required=False)

class AttendanceSerializer(serializers.ModelSerializer):
    employee_code = serializers.CharField(source="employee.employee_code")
    employee_name = serializers.CharField(source="employee.name")
    shift_code = serializers.CharField(source="shift.code")
    punch_in_date = serializers.SerializerMethodField()
    punch_in_time = serializers.SerializerMethodField()
    punch_out_date = serializers.SerializerMethodField()
    punch_out_time = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = [
            "employee_code", "employee_name", "attendance_date", "shift_code",
            "punch_in_date", "punch_in_time", "punch_out_date", "punch_out_time",
            "first_half", "second_half", "total_worked_minutes", "status",
            "is_late", "late_minutes", "is_early", "early_minutes",
            "missing_punchout_flagged", "flag_note",
        ]

    def get_punch_in_date(self, obj):
        return obj.punch_in_timestamp.date() if obj.punch_in_timestamp else None

    def get_punch_in_time(self, obj):
        return obj.punch_in_timestamp.time() if obj.punch_in_timestamp else None

    def get_punch_out_date(self, obj):
        return obj.punch_out_timestamp.date() if obj.punch_out_timestamp else None

    def get_punch_out_time(self, obj):
        return obj.punch_out_timestamp.time() if obj.punch_out_timestamp else None