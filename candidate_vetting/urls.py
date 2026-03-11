from django.urls import path

from .views import TargetVettingView, TargetVettingFormView, TargetFPView

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = 'candidate_vetting'

urlpatterns = [
    path('targets/<int:pk>/vet/<vetting_mode>/', TargetVettingView.as_view(), name='vet'),
    path('targets/<int:pk>/vetchoice/', TargetVettingFormView.as_view(), name='vet_form'),
    path('targets/<int:pk>/checknewphot/', TargetFPView.as_view(), name='checknewphot')
]
