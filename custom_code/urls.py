from django.urls import path

from tom_targets.views import TargetGroupingView, TargetGroupingDeleteView
from .views import TargetGroupingCreateView

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = 'custom_code'

urlpatterns = [
    path('targetgrouping/', TargetGroupingView.as_view(), name='targetgrouping'),
    path('targetgrouping/create/', TargetGroupingCreateView.as_view(), name='create-group'),
    path('targetgrouping/<int:pk>/delete/', TargetGroupingDeleteView.as_view(), name='delete-group'),
]
