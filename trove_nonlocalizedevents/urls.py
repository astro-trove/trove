from django.urls import path
from .views import (
    EventCandidateCreateFromNLEView,
    generate_report,
    generate_selected_candidates_csv,
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
    path("trove_nonlocalizedevents/generate-report/", 
         generate_report, 
         name="generate-report",
    ),
    path("trove_nonlocalizedevents/generate-report-csv/", 
         generate_selected_candidates_csv, 
         name="generate-selected-candidates-csv",
    ),
    path(
        "trove_nonlocalizedevents/create-eventcandidate-from-nle/",
        EventCandidateCreateFromNLEView.as_view(),
        name="eventcandidate-create-from-form",
    ),
    path(
        "trove_nonlocalizedevents/toggle-agn-cache/",
        ToggleAgnCacheView.as_view(),
        name="toggle-agn-cache",
    ),
    path(
        "trove_nonlocalizedevents/toggle-phot-method/",
        TogglePhotMethodCacheView.as_view(),
        name="toggle-phot-method",
    ),
    path(
        "trove_nonlocalizedevents/refresh-candidate-list",
        RefreshCandidateList.as_view(),
        name="refresh-candidate-list"
    ),
    path(
        "trove_nonlocalizedevents/skymap/",
        SkymapPartialView.as_view(),
        name="skymap"
    ),
    path(
        "trove_nonlocalizedevents/vet-multi-progress/",
        VetMultiProgressPartialView.as_view(),
        name="vet-multi-progress"
    ),
    path(
        'trove_nonlocalizedevents/vet-all-cooldown/',
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
