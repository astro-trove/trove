from django.urls import path

from tom_targets.views import TargetGroupingView, TargetGroupingDeleteView
from .views import TargetGroupingCreateView, CandidateListView, TargetReportView, TargetClassifyView, TargetVettingView
from .views import ObservationCreateView, TargetNameSearchView, TargetListView, TargetATLASForcedPhot
from .views import TargetTNSPhotometry, DataProductUploadView, CSSFieldListView, NonLocalizedEventListView
from .views import CSSFieldExportView, CSSFieldSubmitView

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = 'custom_code'

urlpatterns = [
    path('targetgrouping/', TargetGroupingView.as_view(), name='targetgrouping'),
    path('targetgrouping/create/', TargetGroupingCreateView.as_view(), name='create-group'),
    path('targetgrouping/<int:pk>/delete/', TargetGroupingDeleteView.as_view(), name='delete-group'),
    path('candidates/', CandidateListView.as_view(), name='candidates'),
    path('targets/<int:pk>/report/', TargetReportView.as_view(), name='report'),
    path('targets/<int:pk>/classify/', TargetClassifyView.as_view(), name='classify'),
    path('targets/<int:pk>/vet/', TargetVettingView.as_view(), name='vet'),
    path('targets/<int:pk>/runatlasfp/', TargetATLASForcedPhot.as_view(), name='runatlasfp'),
    path('targets/<int:pk>/runtnsphot/', TargetTNSPhotometry.as_view(), name='runtnsphot'),
    path('targets/search/', TargetNameSearchView.as_view(), name='search'),
    path('targets/', TargetListView.as_view(), name='list'),
    path('observations/<str:facility>/create/', ObservationCreateView.as_view(), name='create'),
    path('dataproducts/data/upload/', DataProductUploadView.as_view(), name='upload'),
    path('nonlocalizedevents/', NonLocalizedEventListView.as_view(), name='nonlocalizedevents'),
    path('nonlocalizedevents/<int:localization_id>/cssfields/', CSSFieldListView.as_view(), name='css-fields'),
    path('nonlocalizedevents/<str:event_id>/cssfields/', CSSFieldListView.as_view(), name='css-fields-latest'),
    path('nonlocalizedevents/<int:localization_id>/cssfields/export/', CSSFieldExportView.as_view(), name='css-fields-export'),
    path('nonlocalizedevents/<int:localization_id>/cssfields/submit/', CSSFieldSubmitView.as_view(), name='css-fields-submit'),
]
