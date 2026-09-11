from django import forms, template
from django.conf import settings
from guardian.shortcuts import get_objects_for_user
import plotly.io as pio
import plotly.graph_objs as go
from plotly import colors
from tom_dataproducts.models import ReducedDatum
import numpy as np
from datetime import datetime, timedelta
import re
import logging

from tom_dataproducts.forms import DataShareForm

register = template.Library()
logger = logging.getLogger(__name__)

DECAY_FIT_PARAM_RANGES = dict(
    lum_max=[-np.inf, np.inf],
    peak_time=[-np.inf, np.inf],
    decay_rate=[-np.inf, np.inf],
    t_pre=0,
    t_post=np.inf,
    max_decay_fit_time=100,
    phot_score_snr_min=5,
    min_time_separation=1 / 24,
)
DECAY_FIT_FILTERS = ["g", "r", "i", "z", "F129", "F158", "o", "c"]
DISCOVERY_LINE_STYLE = dict(color='gray', dash='dot', width=1)

# Filter color map for photometry plotting
# Colors chosen to match standard astronomical conventions
COLOR_MAP = {
    'u': '#7f00ff',  # violet
    'U': '#7f00ff',
    'B': '#0000ff',  # blue
    'g': '#00ff00',  # green
    'V': '#008000',  # dark green
    'r': '#ff0000',  # red
    'R': '#ff0000',
    'i': '#8b0000',  # dark red
    'I': '#8b0000',
    'z': '#4a0000',  # very dark red
    'y': '#2a0000',  # near-infrared
    'Y': '#2a0000',
    'J': '#1a0000',
    'H': '#0a0000',
    'K': '#050000',
    'c': '#00ffff',  # ATLAS cyan
    'o': '#ffa500',  # ATLAS orange
    'G': '#00ff00',  # Gaia G-band
    'w': '#808080',  # Pan-STARRS w (wide)
    'Clear': '#808080',
    'clear': '#808080',
    'L': '#808080',
    'F070W': 'C7', # JWST
    'F090W': 'C0',
    'F115W': 'C8',
    'F150W': 'C1',
    'F182M': 'tomato',
    'F200W': 'C2',
    'F250M': 'chocolate',
    'F277W': 'C3',
    'F300M': 'maroon',
    'F335M': 'salmon',
    'F356W': 'C4',
    'F360M': 'crimson',
    'F444W': 'C5',
    'F560W': 'C9',
    'F770W': 'C6',
    'F1000W': 'C7',
    'F1130W': 'C0',
    'F1280W': 'C8',
    'F1500W': 'C1',
    'F1800W': 'C9',
    'F2100W': 'C2',
    'F2550W': 'C3',
    'F062': '#332288', # Roman; colors = David Nichols' Tol palette https://davidmathlogic.com/colorblind/
    'F087': '#117733',
    'F106': '#44AA99',
    'F129': '#88CCEE',
    'F146': '#DDCC77',
    'F158': '#CC6677',
    'F184': '#AA4499',
    'F213': '#882255',
}
MARKER_MAP = {
    'limit': 50,  # arrow-bar-down
    'ATLAS': 2,  # diamond
    'MARS': 1,  # square
    'SAGUARO pipeline': 0,  # circle
    'ZTF': 1,  # square
    'P48': 1,  # square
}

# other marker colors and shapes for sources not listed above
OTHER_MARKERS = list(range(33))  # all filled markers in Plotly
OTHER_MARKERS.remove(6)  # do not use triangle-down, too close to arrow-bar-down
OTHER_COLORS = colors.qualitative.Plotly  # default Plotly color sequence


def _decay_fit_trace(target, nonlocalized_event, t0):
    """Overlay of the powerlaw/broken-powerlaw decay fit for one event's
    photometry, in the same (x, y) space as the detection points, or None if
    there isn't enough data to fit
    """

    from scoring.vet_phot import _get_post_disc_phot, _score_phot

    try:
        allphot = _get_post_disc_phot(
            target_id=target.id,
            nonlocalized_event=nonlocalized_event,
            t_post=DECAY_FIT_PARAM_RANGES["t_post"],
            t_pre=DECAY_FIT_PARAM_RANGES["t_pre"],
        )
        _, _, _, _, fit_model, fit_params = _score_phot(
            allphot=allphot,
            target=target,
            nonlocalized_event=nonlocalized_event,
            param_ranges=DECAY_FIT_PARAM_RANGES,
            filt=DECAY_FIT_FILTERS,
        )
    except Exception:
        logger.exception(
            "Could not compute the decay fit overlay for %s / %s",
            target.name,
            nonlocalized_event.event_id,
        )
        return None

    if fit_model is None:
        return None

    dt_fittable = allphot.dt[allphot.dt > 0]
    dt_max = min(float(dt_fittable.max()), DECAY_FIT_PARAM_RANGES["max_decay_fit_time"])
    xtest_dt = np.linspace(float(dt_fittable.min()), dt_max, 200)
    ytest_mag = fit_model(xtest_dt, *fit_params)
    xtest_abs = [t0 + timedelta(days=float(dt)) for dt in xtest_dt]

    shape = fit_model.__name__.strip("_").replace("_", " ")
    return go.Scatter(
        x=xtest_abs,
        y=ytest_mag,
        mode='lines',
        line=dict(color='black', dash='dash', width=3),
        name=f'{target.name} fit',
        hovertemplate=f'{shape} fit<br>%{{x}}<br>%{{y:.2f}} mag<extra></extra>',
    )


def get_marker_for_photometry_point(label, marker_map, others):
    """
    Get marker properties (color or shape) from a dictionary `marker_map` after parsing the photometry `label`.
    If there is no matching label in the dictionary, pick the next item in `others`.
    """
    base_label = re.sub(' \(.*\)', '', re.sub('[-_].*', '', label))
    if label in marker_map:
        return marker_map[label]
    elif base_label in marker_map:
        return marker_map[base_label]
    else:
        for marker in others:
            if marker not in marker_map.values():
                marker_map[base_label] = marker
                print(marker_map)
                return marker


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


@register.inclusion_tag('tom_dataproducts/partials/data_plot_for_target.html', takes_context=True)
def photometry_for_target(context, target, width=900, height=600, background=None, label_color=None, grid=True):
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
    datums = datums.order_by("source_name", "value__filter") # sort data by telescope/filter name

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
    color_map = COLOR_MAP.copy()
    marker_map = MARKER_MAP.copy()
    other_colors = OTHER_COLORS.copy()
    other_markers = OTHER_MARKERS.copy()
    for source_name, source_values in detections.items():
        marker_symbol = get_marker_for_photometry_point(source_name, marker_map, other_markers)
        for filter_name, filter_values in source_values.items():
            marker_color = get_marker_for_photometry_point(filter_name, color_map, other_colors)
            series = go.Scatter(
                x=filter_values['time'],
                y=filter_values['magnitude'],
                mode='markers',
                marker_color=marker_color,
                marker_symbol=marker_symbol,
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
            marker_color = get_marker_for_photometry_point(filter_name, color_map, other_colors)
            series = go.Scatter(
                x=filter_values['time'],
                y=filter_values['limit'],
                mode='markers',
                opacity=0.5,
                marker_color=marker_color,
                marker_symbol=MARKER_MAP['limit'],
                name=f'{source_name} {filter_name}',
            )
            plot_data.append(series)
            all_ydata.append(np.array(filter_values['limit'], float))

    # Add a constant legend item for limit markers.
    plot_data.append(go.Scatter(
        x=[None],
        y=[None],
        mode='markers',
        marker=dict(color='gray', symbol='triangle-down'),
        name='limits',
        showlegend=True,
        hoverinfo='none',
    ))

    # scale the y-axis manually so that we know the range ahead of time and can scale the secondary y-axis to match
    if all_ydata:
        all_ydata = np.concatenate(all_ydata)
        ymin = np.nanpercentile(all_ydata, 0.1)
        ymax = np.nanpercentile(all_ydata, 99.9)
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
        if not candidate.nonlocalizedevent.sequences.last().details['time'][-1] in ("z", "Z"):
            t0 = datetime.strptime(candidate.nonlocalizedevent.sequences.last().details['time']+"Z", '%Y-%m-%dT%H:%M:%S.%f%z')
        else:
            t0 = datetime.strptime(candidate.nonlocalizedevent.sequences.last().details['time'], '%Y-%m-%dT%H:%M:%S.%f%z')
        # a real trace instead of add_vline's shape + a separate invisible
        # legend-only trace: one object draws the line AND legends it,
        # rather than two objects each doing half the job
        fig.add_trace(go.Scatter(
            x=[t0, t0],
            y=[ymin_view, ymax_view],
            mode='lines',
            line=DISCOVERY_LINE_STYLE,
            name=f'{candidate.nonlocalizedevent.event_id} discovery',
        ))
        fig.add_annotation(
            x=t0, y=ymax_view, text=candidate.nonlocalizedevent.event_id, showarrow=False, yshift=10,
        )

        fit_trace = _decay_fit_trace(target, candidate.nonlocalizedevent, t0)
        if fit_trace is not None:
            fig.add_trace(fit_trace)

    return {
        'target': target,
        'plot': pio.to_html(fig, full_html=False, div_id='photometry_plot')
    }

@register.inclusion_tag('tom_dataproducts/partials/photometry_datalist_for_target.html', takes_context=True)
def get_photometry_data(context, target, target_share=False):
    """
    Displays a table of the all photometric points for a target.
    """

    photometry = ReducedDatum.objects.filter(data_type='photometry', target=target).order_by('-timestamp')
    if not settings.TARGET_PERMISSIONS_ONLY:
        photometry = get_objects_for_user(
            context["request"].user,
            "tom_dataproducts.view_reduceddatum",
            klass=photometry,
        )
    
    data = []
    for reduced_datum in photometry:
        rd_data = {'id': reduced_datum.pk,
                   'timestamp': reduced_datum.timestamp,
                   'source': reduced_datum.source_name,
                   'filter': reduced_datum.value.get('filter', ''),
                   'telescope': reduced_datum.value.get('telescope', ''),
                   'error': reduced_datum.value.get('error', reduced_datum.value.get('magnitude_error', ''))
                   }

        if 'limit' in reduced_datum.value.keys():
            rd_data['magnitude'] = reduced_datum.value['limit']
            rd_data['limit'] = True
        else:
            rd_data['magnitude'] = reduced_datum.value['magnitude']
            rd_data['limit'] = False
        data.append(rd_data)

    band_filters = np.unique([phot_dict["filter"] for phot_dict in data])
    sources = np.unique([phot_dict["source"] for phot_dict in data])

    initial = {'submitter': context['request'].user,
               'target': target,
               'data_type': 'photometry',
               'share_title': f"Updated data for {target.name} from {getattr(settings, 'TOM_NAME', 'TOM Toolkit')}.",
               }
    form = DataShareForm(initial=initial)
    form.fields['data_type'].widget = forms.HiddenInput()

    sharing = getattr(settings, "DATA_SHARING", None)
    hermes_sharing = sharing and sharing.get('hermes', {}).get('HERMES_API_KEY')

    context = {
        'data': data,
        'target': target,
        'target_data_share_form': form,
        'sharing_destinations': form.fields['share_destination'].choices,
        'hermes_sharing': hermes_sharing,
        'target_share': target_share,
        'band_filters': band_filters,
        'sources': sources
    }
    return context


@register.filter
def format_mag(datum, d=2):
    try:
        if datum.get('magnitude'):
            datum['magnitude'] = float(datum['magnitude'])
            if datum.get('error'):
                datum['error'] = float(datum['error'])
                display_str = f'{{magnitude:.{d}f}} ± {{error:.{d}f}}'
            elif datum.get('limit'):
                display_str = f'> {{magnitude:.{d}f}}'
            else:
                display_str = f'{{magnitude:.{d}f}}'
            return display_str.format(**datum)
    except:
        print("Unable to format magnitude")

@register.filter
def error_to_snr(error):
    return 2.5 / np.log(10.) / error