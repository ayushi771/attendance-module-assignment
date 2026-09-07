from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.pagination import PageNumberPagination

from .models import Shift, Employee, Attendance
from .serializers import ShiftSerializer, EmployeeSerializer, PunchRequestSerializer, AttendanceSerializer
from . import services
from .models import PunchLog
from .serializers import PunchLogSerializer
from django.shortcuts import render


class LargeResultsSetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "limit"
    max_page_size = 500

class ShiftListCreateView(generics.ListCreateAPIView):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer

class  EmployeeListCreateView(generics.ListCreateAPIView):
    queryset = Employee.objects.all()
    serializer_class = EmployeeSerializer
    pagination_class = LargeResultsSetPagination  

class PunchInView(APIView):
    def post(self, request):
        serializer = PunchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            employee = Employee.objects.get(employee_code=data["employee_code"])
        except Employee.DoesNotExist:
            return Response({"error": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            attendance = services.punch_in(employee, data.get("timestamp"))
        except services.AttendanceError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        attendance_serializer = AttendanceSerializer(attendance)
        return Response(attendance_serializer.data, status=status.HTTP_200_OK)

class PunchOutView(APIView):
    def post(self, request):
        serializer = PunchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            employee = Employee.objects.get(employee_code=data["employee_code"])
        except Employee.DoesNotExist:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            attendance = services.punch_out(employee, data.get("timestamp"))
        except services.AttendanceError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(AttendanceSerializer(attendance).data)


class AttendanceListView(generics.ListAPIView):
    serializer_class = AttendanceSerializer
    pagination_class = LargeResultsSetPagination

    def get_queryset(self):
        qs = Attendance.objects.select_related("employee", "shift").order_by(
            "-attendance_date", "employee_id"
        )
        employee_code = self.request.query_params.get("employee_code")
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")

        if employee_code:
            qs = qs.filter(employee__employee_code=employee_code)
        if date_from:
            qs = qs.filter(attendance_date__gte=date_from)
        if date_to:
            qs = qs.filter(attendance_date__lte=date_to)
        return qs    


class RecentPunchLogsView(generics.ListAPIView):
    serializer_class = PunchLogSerializer

    def get_queryset(self):
        return PunchLog.objects.select_related("employee").order_by("-punch_timestamp")[:20]


def dashboard(request):
    return render(request, "dashboard.html")    