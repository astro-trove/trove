from django.urls import path
from .views import EventCandidateAutocompleteView, generate_report

app_name = "trove_nonlocalizedevents"

urlpatterns = [
    path(
        "eventcandidate-autocomplete/",
        EventCandidateAutocompleteView.as_view(),
        name="eventcandidate-autocomplete",
    ),
    path("generate-report/", generate_report, name="generate-report"),
]
