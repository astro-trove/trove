from django.urls import path
from .views import (
    CustomTargetCreateView,
    NLEAutocompleteView,
    TargetAutocompleteView,
    TroveTargetListView,
    TargetReportView,
    TargetClassifyView,
    TargetNameSearchView
)

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
    path(
        "",
        TroveTargetListView.as_view(),
        name="targetlist"
    )
    path('targets/<int:pk>/report/', TargetReportView.as_view(), name='report'),
    path('targets/<int:pk>/classify/', TargetClassifyView.as_view(), name='classify'),
    path('targets/search/<str:search_str>', TargetNameSearchView.as_view(), name='search'),

]
