from django.urls import path

from .views import (
    TargetVettingView,
    TargetVettingFormView,
    TargetVettingAllView,
    TargetVettingAllFormView,
    TargetFPView,
    TargetRedshiftUpdateFormView,
)

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = "scoring"

urlpatterns = [
    path(
        "targets/<int:pk>/vet/<vetting_mode>/", TargetVettingView.as_view(), name="vet"
    ),
    path(
        "targets/<int:pk>/vetchoice/", TargetVettingFormView.as_view(), name="vet_form"
    ),
    path("targets/<int:pk>/checknewphot/", TargetFPView.as_view(), name="checknewphot"),
    path(
        "targets/<int:pk>/updatez/",
        TargetRedshiftUpdateFormView.as_view(),
        name="updatez",
    ),
    path(
        "eventcandidates/?nonlocalizedevent=<int:pk>/vetall/<vetting_mode>/",
        TargetVettingAllView.as_view(),
        name="vet_all",
    ),
    path(
        "eventcandidates/?nonlocalizedevent=<int:pk>/vetallchoice/",
        TargetVettingAllFormView.as_view(),
        name="vet_all_form",
    ),
]
