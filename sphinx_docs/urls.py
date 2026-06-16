from django.urls import re_path
from .views import DocsView

urlpatterns = [
    re_path(r"^(?P<path>.*)$", DocsView.as_view(), name="docs"),
]
