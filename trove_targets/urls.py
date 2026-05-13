from django.urls import path
from .views import CustomTargetCreateView, NLEAutocompleteView, TargetAutocompleteView

app_name = "trove_targets"

urlpatterns = [
    path("create/", CustomTargetCreateView.as_view(), name="create"),
    path(
        "nonlocalizedevent-autocomplete/",
        NLEAutocompleteView.as_view(),
        name="nonlocalizedevent-autocomplete",
    ),
    path(
        "target-autocomplete/",
        TargetAutocompleteView.as_view(),
        name="target-autocomplete",
    ),
    # ... other urls
]
