from django.urls import path
from tom_common.api_router import SharedAPIRootRouter
from .views import TreasureMapPointingListView

router = SharedAPIRootRouter()

app_name = 'treasuremap'

urlpatterns = [
    path('', TreasureMapPointingListView.as_view(), name='list'),
]
