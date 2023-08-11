from django.urls import path
from tom_common.api_router import SharedAPIRootRouter
from .views import SurveyObservationListView

router = SharedAPIRootRouter()

app_name = 'surveys'

urlpatterns = [
    path('observations/', SurveyObservationListView.as_view(), name='observations'),
]