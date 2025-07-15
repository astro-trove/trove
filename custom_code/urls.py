from django.urls import path

from .views import TargetReportView, TargetClassifyView, TargetVettingView
from .views import TargetNameSearchView
from .views import GWListView, GRBListView, NeutrinoListView, UnknownListView
from .views import EventCandidateCreateView
from tom_nonlocalizedevents.views import SupereventIdView

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = 'custom_code'

urlpatterns = [
    path('targets/<int:pk>/report/', TargetReportView.as_view(), name='report'),
    path('targets/<int:pk>/classify/', TargetClassifyView.as_view(), name='classify'),
    path('targets/<int:pk>/vet/', TargetVettingView.as_view(), name='vet'),
    path('targets/search/', TargetNameSearchView.as_view(), name='search'),
    path('nonlocalizedevents/gw/', GWListView.as_view(), name='gw-list'),
    path('nonlocalizedevents/grb/', GRBListView.as_view(), name='grb-list'),
    path('nonlocalizedevents/neutrino/', NeutrinoListView.as_view(), name='neutrino-list'),
    path('nonlocalizedevents/unknown/', UnknownListView.as_view(), name='unknown-list'),
    path('nonlocalizedevents/<str:event_id>/', SupereventIdView.as_view(), name='event-detail'),  # prioritize event_id
    path('nonlocalizedevents/<str:event_id>/createcandidate/<int:target_id>/', EventCandidateCreateView.as_view(), name='create-candidate'),
]
