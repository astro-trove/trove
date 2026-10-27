from django.urls import path
from .views import (
    EventCandidateCreateFromNLEView,
    generate_report,
    ToggleAgnCacheView,
    TogglePhotMethodCacheView,
    RefreshCandidateList,
    SkymapPartialView,
    VetMultiProgressPartialView,
    vet_all_cooldown_notice,
    GWListView,
    GRBListView,
    NeutrinoListView,
    UnknownListView,
    EventCandidateCreateView,
    GWNonLocalizedEventOrTargetNameSearchView,
)

from tom_nonlocalizedevents.views import SupereventIdView
from trove_nonlocalizedevents.views import EventCandidateListView

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
        "vet-multi-progress/",
        VetMultiProgressPartialView.as_view(),
        name="vet-multi-progress"
    ),
    path(
        'vet-all-cooldown/',
        vet_all_cooldown_notice,
        name='vet_all_cooldown_notice'
    ),
    path('nonlocalizedevents/gw/', GWListView.as_view(), name='gw-list'),
    path('nonlocalizedevents/gwsearch/<str:search_str>', GWListView.as_view(), name='gw-list-search'),
    path('nonlocalizedevents/grb/', GRBListView.as_view(), name='grb-list'),
    path('nonlocalizedevents/neutrino/', NeutrinoListView.as_view(), name='neutrino-list'),
    path('nonlocalizedevents/unknown/', UnknownListView.as_view(), name='unknown-list'),
    path('nonlocalizedevents/<str:event_id>/', SupereventIdView.as_view(), name='event-detail'),  # prioritize event_id
    path('nonlocalizedevents/<str:event_id>/createcandidate/<int:target_id>/', EventCandidateCreateView.as_view(), name='create-candidate'),
    path('eventcandidates/', EventCandidateListView.as_view(), name='event-candidates'),
    path('search/', GWNonLocalizedEventOrTargetNameSearchView.as_view(), name='gwnle-or-target-search'),
]
