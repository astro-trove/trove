from django.urls import path
from .views import (
    EventCandidateCreateFromNLEView,
    generate_report,
    ToggleAgnCacheView,
)

app_name = "trove_nonlocalizedevents"

urlpatterns = [
    path("generate-report/", generate_report, name="generate-report"),
    path(
        "create-eventcandidate-from-nle/",
        EventCandidateCreateFromNLEView.as_view(),
        name="eventcandidate-create-from-form",
    ),
    path(
        "toggle-agn-cache/",
        ToggleAgnCacheView.as_view(),
        name="toggle-agn-cache",
    ),
]
