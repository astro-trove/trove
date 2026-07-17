"""django URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path, include
from .api import api

urlpatterns = [
    path("", include("custom_code.urls")),
    path("targets/", include("trove_targets.urls", namespace="trove_targets")),
    path("", include("tom_common.urls")),
    path("", include("scoring.urls")),
    path(
        "trove_nonlocalizedevents/",
        include("trove_nonlocalizedevents.urls", namespace="trove_nonlocalizedevents"),
    ),
    path(
        "nonlocalizedevents/",
        include("tom_nonlocalizedevents.urls", namespace="nonlocalizedevents"),
    ),
    path("api/", api.urls),
    path("docs/", include("sphinx_docs.urls", namespace="sphinx_docs")),
]
