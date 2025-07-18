from django.urls import path

from .views import TargetVettingView

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = 'candidate_vetting'

urlpatterns = [
    path('targets/<int:pk>/vet/', TargetVettingView.as_view(), name='vet'),
]
