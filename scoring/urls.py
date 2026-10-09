from django.urls import path

from .views import (
    TargetVettingView,
    TargetVettingFormView,
    TargetVettingSelectedFormView,
    TargetVettingAllFormView,
    TargetFPView,
    TargetRedshiftUpdateFormView,
    VettingMethodsPartialView,
    NonLocalizedEventAssociateTargetsFormView,
)

from tom_common.api_router import SharedAPIRootRouter

router = SharedAPIRootRouter()

app_name = "scoring"

urlpatterns = [
    path(
        "targets/<int:pk>/vet/<vetting_mode>/", 
        TargetVettingView.as_view(), 
        name="vet"
    ),
    path(
        "targets/<int:pk>/vetchoice/", 
        TargetVettingFormView.as_view(), 
        name="vet_form"
    ),
    path(
        "targets/<int:pk>/vetchoice/methods/",
        VettingMethodsPartialView.as_view(),
        name="vet_methods",
    ),
    path("targets/<int:pk>/checknewphot/", 
         TargetFPView.as_view(), 
         name="checknewphot"),
    path(
        "targets/<int:pk>/updatez/",
        TargetRedshiftUpdateFormView.as_view(),
        name="updatez",
    ),
    path(
        "eventcandidates/?nonlocalizedevent=<int:pk>/vetselected/",
        TargetVettingSelectedFormView.as_view(),
        name="vet_selected_form",
    ),
    path(
        "eventcandidates/?nonlocalizedevent=<int:pk>/vetall/",
        TargetVettingAllFormView.as_view(),
        name="vet_all_form",
    ),
    path(
         "eventcandidates/?nonlocalizedevent=<int:pk>/associatetargetschoice/", 
         NonLocalizedEventAssociateTargetsFormView.as_view(), 
         name="nle_associate_targets_form"
    ),

]
