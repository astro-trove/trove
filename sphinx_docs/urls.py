from django.urls import re_path, path
from .views import DocsView

app_name = "sphinx_docs"

urlpatterns = [
    path("", DocsView.as_view(), name="docs"),
    re_path(r"^(?P<path>.+)$", DocsView.as_view(), name="docs_page"),
]
