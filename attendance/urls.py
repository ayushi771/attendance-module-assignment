from django.urls import path
from . import views

urlpatterns = [
    path("shifts", views.ShiftListCreateView.as_view()),
    path("employees", views.EmployeeListCreateView.as_view()),
    path("punch-in", views.PunchInView.as_view()),
    path("punch-out", views.PunchOutView.as_view()),
    path("attendance", views.AttendanceListView.as_view()),
    path("punch-logs", views.RecentPunchLogsView.as_view()),
]