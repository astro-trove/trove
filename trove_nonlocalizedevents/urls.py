from django.urls import path
from .views import (
    EventCandidateCreateFromNLEView,
    generate_report,
    ToggleAgnCacheView,
    TogglePhotMethodCacheView,
    RefreshCandidateList,
    SkymapPartialView,
    vet_all_cooldown_notice
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
    path(
        "toggle-phot-method/",
        TogglePhotMethodCacheView.as_view(),
        name="toggle-phot-method",
    ),
    path(
        "refresh-candidate-list",
        RefreshCandidateList.as_view(),
        name="refresh-candidate-list"
    ),
    path(
        "skymap/",
        SkymapPartialView.as_view(),
        name="skymap"
    ),
    path(
        'vet-all-cooldown/',
        vet_all_cooldown_notice,
        name='vet_all_cooldown_notice'
    ),
]
