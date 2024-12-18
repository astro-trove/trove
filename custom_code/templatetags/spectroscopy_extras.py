import logging

from django import template
from django.conf import settings
from datetime import datetime
from guardian.shortcuts import get_objects_for_user
from plotly import offline
import plotly.graph_objs as go

from tom_dataproducts.forms import DataShareForm
from tom_dataproducts.models import DataProduct, ReducedDatum
from tom_dataproducts.processors.data_serializers import SpectrumSerializer

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

register = template.Library()


# copied from tom_base except for datum.source_name was added to the "name" of each data series
@register.inclusion_tag('tom_dataproducts/partials/spectroscopy_for_target.html', takes_context=True)
def spectroscopy_for_target(context, target, dataproduct=None):
    """
    Renders a spectroscopic plot for a ``Target``. If a ``DataProduct`` is specified, it will only render a plot with
    that spectrum.
    """
    try:
        spectroscopy_data_type = settings.DATA_PRODUCT_TYPES['spectroscopy'][0]
    except (AttributeError, KeyError):
        spectroscopy_data_type = 'spectroscopy'
    spectral_dataproducts = DataProduct.objects.filter(target=target,
                                                       data_product_type=spectroscopy_data_type)
    if dataproduct:
        spectral_dataproducts = DataProduct.objects.get(data_product=dataproduct)

    plot_data = []
    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(data_product__in=spectral_dataproducts)
    else:
        datums = get_objects_for_user(context['request'].user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(data_product__in=spectral_dataproducts))
    for datum in datums.order_by('timestamp'):
        deserialized = SpectrumSerializer().deserialize(datum.value)
        plot_data.append(go.Scatter(
            x=deserialized.wavelength.value,
            y=deserialized.flux.value,
            name=f"{datetime.strftime(datum.timestamp, '%Y-%m-%d')} {datum.source_name}"
        ))

    layout = go.Layout(
        height=600,
        width=700,
        xaxis=dict(
            tickformat="d"
        ),
        yaxis=dict(
            tickformat=".1g"
        )
    )
    return {
        'target': target,
        'products': datums,
        'plot': offline.plot(go.Figure(data=plot_data, layout=layout), output_type='div', show_link=False)
    }


# mostly copied from dataproduct_list_for_target in tom_base, but switched to ReducedDatum and filtered to only spectra
@register.inclusion_tag('tom_dataproducts/partials/spectroscopy_datalist_for_target.html', takes_context=True)
def get_spectroscopy_data(context, target):
    """
    Given a ``Target``, returns a list of ``DataProduct`` objects associated with that ``Target``
    """
    if settings.TARGET_PERMISSIONS_ONLY:
        target_datums_for_user = target.reduceddatum_set.all()
    else:
        target_datums_for_user = get_objects_for_user(
            context['request'].user, 'tom_dataproducts.view_reduceddatum', klass=target.reduceddatum_set.all())
    spectroscopy_for_user = target_datums_for_user.filter(data_type='spectroscopy').order_by('timestamp')

    initial = {'submitter': context['request'].user,
               'target': target,
               'share_title': f"Updated data for {target.name}."}
    form = DataShareForm(initial=initial)

    return {
        'datums': spectroscopy_for_user,
        'target': target,
        'sharing_destinations': form.fields['share_destination'].choices,
        'data_product_share_form': form
    }
