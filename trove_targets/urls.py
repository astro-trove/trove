from django.urls import path
from .views import CustomTargetCreateView, NLEAutocompleteView

app_name = 'trove_targets'

urlpatterns = [
    path('create/', CustomTargetCreateView.as_view(), name='create'),
    path(
        'nonlocalizedevent-autocomplete/',
        NLEAutocompleteView.as_view(),
        name='nonlocalizedevent-autocomplete'
    ),
    # ... other urls
]
