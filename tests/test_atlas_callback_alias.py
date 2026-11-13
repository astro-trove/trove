"""
The temporary /saguaro_tom/ alias for the ATLAS forced photometry callback must
reach the same view as the real endpoint.
"""
from django.urls import resolve, reverse
from tom_dataproducts.api_views import ATLASProcessorView


def test_real_atlas_callback_url_resolves():
    assert resolve("/dataproducts/api/atlas_processor/").func.view_class is ATLASProcessorView

def test_reverse_still_returns_real_url():
    url = reverse("dataproducts:atlas-processor")
    assert url.endswith("/dataproducts/api/atlas_processor/")
    assert "saguaro_tom" not in url
