from urllib.parse import urlencode

from django import template
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.shortcuts import reverse
from django.utils import timezone
from datetime import datetime, timedelta
from guardian.shortcuts import get_objects_for_user
from plotly import offline
import plotly.graph_objs as go
from io import BytesIO
from PIL import Image, ImageDraw
import base64

from tom_dataproducts.forms import DataProductUploadForm
from tom_dataproducts.models import DataProduct, ReducedDatum
from tom_observations.models import ObservationRecord
from tom_targets.models import Target

register = template.Library()

@register.inclusion_tag('tom_dataproducts/partials/recent_photometry.html')
def recent_photometry(target, limit=1):
    """
    Displays a table of the most recent photometric points for a target.
    """

    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]).order_by('-timestamp')[:limit]
    else:
        datums = get_objects_for_user(context['request'].user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])).order_by('-timestamp')[:limit]
    recent_det = {'data': []}
    for datum in datums:
        if 'magnitude' in datum.value.keys():
            recent_det['data'].append({'timestamp': datum.timestamp, 'magnitude': datum.value['magnitude']})
        elif 'limit' in datum.value.keys():
            recent_det['data'].append({'timestamp': datum.timestamp, 'limit': datum.value['limit']})
        else:
            continue

    if len(recent_det['data'])>0:
        return recent_det
    else:
        return

    # if magnitude in photometry.keys():
    #     return {'data': [{'timestamp': rd.timestamp, 'magnitude': rd.value['magnitude']} for rd in photometry]}
    # else:
    #     return


@register.inclusion_tag('tom_dataproducts/partials/photometry_for_target.html', takes_context=True)
def photometry_for_target(context, target, width=700, height=600, background=None, label_color=None, grid=True):
    """
    Renders a photometric plot for a target.

    This templatetag requires all ``ReducedDatum`` objects with a data_type of ``photometry`` to be structured with the
    following keys in the JSON representation: magnitude, error, filter

    :param width: Width of generated plot
    :type width: int

    :param height: Height of generated plot
    :type width: int

    :param background: Color of the background of generated plot. Can be rgba or hex string.
    :type background: str

    :param label_color: Color of labels/tick labels. Can be rgba or hex string.
    :type label_color: str

    :param grid: Whether to show grid lines.
    :type grid: bool
    """

    color_map = {
        'r': 'red',
        'g': 'green',
        'i': 'black',
        'c': 'blue',
        'o': 'orange'
    }

    photometry_data = {}
    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    else:
        datums = get_objects_for_user(context['request'].user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))

    for datum in datums:
        photometry_data.setdefault(datum.value['filter'], {})
        photometry_data[datum.value['filter']].setdefault('time', []).append(datum.timestamp)
        photometry_data[datum.value['filter']].setdefault('magnitude', []).append(datum.value.get('magnitude'))
        photometry_data[datum.value['filter']].setdefault('error', []).append(datum.value.get('error'))
        photometry_data[datum.value['filter']].setdefault('limit', []).append(datum.value.get('limit'))

    plot_data = []
    for filter_name, filter_values in photometry_data.items():
        if filter_values['magnitude']:
            series = go.Scatter(
                x=filter_values['time'],
                y=filter_values['magnitude'],
                mode='markers',
                marker=dict(color=color_map.get(filter_name)),
                name=filter_name,
                error_y=dict(
                    type='data',
                    array=filter_values['error'],
                    visible=True
                )
            )
            plot_data.append(series)
        if filter_values['limit']:
            series = go.Scatter(
                x=filter_values['time'],
                y=filter_values['limit'],
                mode='markers',
                opacity=0.5,
                marker=dict(color=color_map.get(filter_name)),
                marker_symbol=6,  # upside down triangle
                name=filter_name + ' non-detection',
            )
            plot_data.append(series)

    layout = go.Layout(
        yaxis=dict(autorange='reversed'),
        height=height,
        width=width,
        paper_bgcolor=background,
        plot_bgcolor=background

    )
    layout.legend.font.color = label_color
    fig = go.Figure(data=plot_data, layout=layout)
    fig.update_yaxes(showgrid=grid, color=label_color, showline=True, linecolor=label_color, mirror=True)
    fig.update_xaxes(showgrid=grid, color=label_color, showline=True, linecolor=label_color, mirror=True)
    return {
        'target': target,
        'plot': offline.plot(fig, output_type='div', show_link=False)
    }
