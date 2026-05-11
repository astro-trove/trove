from django.urls import path
from .views import EventCandidateAutocompleteView

app_name = "trove_nonlocalizedevents"

urlpatterns = [
    path(
        "eventcandidate-autocomplete/",
        EventCandidateAutocompleteView.as_view(),
        name="eventcandidate-autocomplete",
    ),
    # ... other urls
]
