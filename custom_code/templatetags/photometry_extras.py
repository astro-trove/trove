from django import template
from django.conf import settings
from guardian.shortcuts import get_objects_for_user
from plotly import offline
import plotly.graph_objs as go
from plotly import colors
from tom_dataproducts.models import ReducedDatum
import numpy as np
from datetime import datetime

register = template.Library()

COLOR_MAP = {
    'g': 'green',
    'r': 'red',
    'i': 'black',
    'c': 'dodgerblue',
    'o': 'orange',
    'orange': 'orange',
    'G': 'darkseagreen',
    'cyan': 'dodgerblue'
}
MARKER_MAP = {
    'limit': 50,  # arrow-bar-down
    'ATLAS': 2,  # diamond
    'MARS': 1,  # square
    'SAGUARO pipeline': 0,  # circle
    'ZTF': 1,  # square
}
OTHER_MARKERS = list(range(33))  # all filled markers in Plotly
OTHER_MARKERS.remove(6)  # do not use triangle-down, too close to arrow-bar-down
OTHER_COLORS = colors.qualitative.Plotly  # default Plotly color sequence


@register.inclusion_tag('tom_dataproducts/partials/recent_photometry.html', takes_context=True)
def recent_photometry(context, target, limit=1):
    """
    Displays a table of the most recent photometric points for a target.
    """

    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    else:
        datums = get_objects_for_user(context['request'].user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))
    recent_det = {'data': []}
    for datum in datums.order_by('-timestamp')[:limit]:
        if 'magnitude' in datum.value.keys():
            phot_point = {'timestamp': datum.timestamp, 'magnitude': datum.value['magnitude']}
        elif 'limit' in datum.value.keys():
            phot_point = {'timestamp': datum.timestamp, 'limit': datum.value['limit']}
        else:
            continue

        if target.distance is not None:
            dm = 5. * (np.log10(target.distance) + 5.)
            phot_point['absmag'] = (phot_point.get('magnitude') or phot_point.get('limit')) - dm

        recent_det['data'].append(phot_point)

    return recent_det


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
    if settings.TARGET_PERMISSIONS_ONLY:
        datums = ReducedDatum.objects.filter(target=target, data_type=settings.DATA_PRODUCT_TYPES['photometry'][0])
    else:
        datums = get_objects_for_user(context['request'].user,
                                      'tom_dataproducts.view_reduceddatum',
                                      klass=ReducedDatum.objects.filter(
                                        target=target,
                                        data_type=settings.DATA_PRODUCT_TYPES['photometry'][0]))

    detections = {}
    limits = {}
    for datum in datums:
        if 'magnitude' in datum.value:
            detections.setdefault(datum.source_name, {})
            detections[datum.source_name].setdefault(datum.value['filter'], {})
            filter_data = detections[datum.source_name][datum.value['filter']]
            filter_data.setdefault('time', []).append(datum.timestamp)
            filter_data.setdefault('magnitude', []).append(datum.value['magnitude'])
            filter_data.setdefault('error', []).append(datum.value.get('error', 0.))
        elif 'limit' in datum.value:
            limits.setdefault(datum.source_name, {})
            limits[datum.source_name].setdefault(datum.value['filter'], {})
            filter_data = limits[datum.source_name][datum.value['filter']]
            filter_data.setdefault('time', []).append(datum.timestamp)
            filter_data.setdefault('limit', []).append(datum.value['limit'])

    plot_data = []
    all_ydata = []
    for source_name, source_values in detections.items():
        for filter_name, filter_values in source_values.items():
            # get unique color and marker for this data series
            if filter_name not in COLOR_MAP:
                for new_color in OTHER_COLORS:
                    if new_color not in COLOR_MAP.values():
                        COLOR_MAP[filter_name] = new_color
                        break
            if source_name not in MARKER_MAP:
                for new_marker in OTHER_MARKERS:
                    if new_marker not in MARKER_MAP.values():
                        MARKER_MAP[source_name] = new_marker
                        break
            series = go.Scatter(
                x=filter_values['time'],
                y=filter_values['magnitude'],
                mode='markers',
                marker_color=COLOR_MAP.get(filter_name),
                marker_symbol=MARKER_MAP.get(source_name),
                name=f'{source_name} {filter_name}',
                error_y=dict(
                    type='data',
                    array=filter_values['error'],
                    visible=True
                )
            )
            plot_data.append(series)
            mags = np.array(filter_values['magnitude'], float)  # converts None --> nan (as well as any strings)
            errs = np.array(filter_values['error'], float)
            errs[np.isnan(errs)] = 0.  # missing errors treated as zero
            all_ydata.append(mags + errs)
            all_ydata.append(mags - errs)
    for source_name, source_values in limits.items():
        for filter_name, filter_values in source_values.items():
            # get unique color for this data series
            if filter_name not in COLOR_MAP:
                for new_color in OTHER_COLORS:
                    if new_color not in COLOR_MAP.values():
                        COLOR_MAP[filter_name] = new_color
                        break
            series = go.Scatter(
                x=filter_values['time'],
                y=filter_values['limit'],
                mode='markers',
                opacity=0.5,
                marker_color=COLOR_MAP.get(filter_name),
                marker_symbol=MARKER_MAP['limit'],
                name=f'{source_name} {filter_name} limits',
            )
            plot_data.append(series)
            all_ydata.append(np.array(filter_values['limit'], float))

    # scale the y-axis manually so that we know the range ahead of time and can scale the secondary y-axis to match
    if all_ydata:
        all_ydata = np.concatenate(all_ydata)
        ymin = np.nanmin(all_ydata)
        ymax = np.nanmax(all_ydata)
        yrange = ymax - ymin
        ymin_view = ymin - 0.05 * yrange
        ymax_view = ymax + 0.05 * yrange
    else:
        ymin_view = 0.
        ymax_view = 0.
    yaxis = {
        'title': 'Apparent Magnitude',
        'range': (ymax_view, ymin_view),
        'showgrid': grid,
        'color': label_color,
        'showline': True,
        'linecolor': label_color,
        'mirror': True,
        'zeroline': False,
    }
    if target.distance is not None:
        dm = 5. * (np.log10(target.distance) + 5.)
        yaxis2 = {
            'title': 'Absolute Magnitude',
            'range': (ymax_view - dm, ymin_view - dm),
            'showgrid': False,
            'overlaying': 'y',
            'side': 'right',
            'zeroline': False,
        }
        plot_data.append(go.Scatter(x=[], y=[], yaxis='y2'))  # dummy data set for abs mag axis
    else:
        yaxis2 = None

    layout = go.Layout(
        xaxis={
            'showgrid': grid,
            'color': label_color,
            'showline': True,
            'linecolor': label_color,
            'mirror': True,
        },
        yaxis=yaxis,
        yaxis2=yaxis2,
        height=height,
        width=width,
        paper_bgcolor=background,
        plot_bgcolor=background,
        legend={
            'font_color': label_color,
            'xanchor': 'center',
            'yanchor': 'bottom',
            'x': 0.5,
            'y': 1.,
            'orientation': 'h',
        }
    )
    fig = go.Figure(data=plot_data, layout=layout)

    for candidate in target.eventcandidate_set.all():
        t0 = datetime.strptime(candidate.nonlocalizedevent.sequences.last().details['time'], '%Y-%m-%dT%H:%M:%S.%f%z')
        fig.add_vline(t0.timestamp() * 1000., annotation_text=candidate.nonlocalizedevent.event_id)

    return {
        'target': target,
        'plot': offline.plot(fig, output_type='div', show_link=False)
    }
