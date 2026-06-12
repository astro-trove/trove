from django.urls import path
from .views import DocsView

urlpatterns = [
    path("", DocsView.as_view(), name="docs"),
    # path("", views.DocsView.as_view(), name="docs_index"),
]
