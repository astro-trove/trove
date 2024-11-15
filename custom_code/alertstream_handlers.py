from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence
from tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler import handle_igwn_message
from django.contrib.auth.models import Group
from django.conf import settings
from twilio.rest import Client
from email.mime.text import MIMEText
import requests
import smtplib
import logging
import json
from .templatetags.nonlocalizedevent_extras import format_inverse_far, format_distance, get_most_likely_class
from .healpix_utils import update_all_credible_region_percents_for_survey_fields, create_elliptical_localization
from .cssfield_selection import calculate_footprint_probabilities
from .models import CredibleRegionContour, Profile
from astropy.table import Table
from io import BytesIO
import astropy_healpix as ah
import numpy as np
import traceback

logger = logging.getLogger(__name__)

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

ALERT_TEXT_INTRO = """{{most_likely_class}} {{seq.event_subtype}} v{{seq.sequence_id}}
{{nle.event_id}} ({{significance}})
{{time}}
1/FAR = {{inverse_far}}
"""

ALERT_TEXT_LOCALIZATION = """Distance = {{distance}}
50% Area = {{seq.localization.area_50:.0f}} deg²
90% Area = {{seq.localization.area_90:.0f}} deg²
"""

ALERT_TEXT_EXTERNAL_COINCIDENCE = """Distance (comb.) = {{distance_external}}
50% Area (comb.) = {{seq.external_coincidence.localization.area_50:.0f}} deg²
90% Area (comb.) = {{seq.external_coincidence.localization.area_90:.0f}} deg²
"""

ALERT_TEXT_CLASSIFICATION = """Has NS = {{HasNS:.0%}}
Has Mass Gap = {{HasMassGap:.0%}}
Has Remnant = {{HasRemnant:.0%}}
BNS = {{BNS:.0%}}
NSBH = {{NSBH:.0%}}
BBH = {{BBH:.0%}}
Terrestrial = {{Terrestrial:.0%}}
"""

# links for Slack
ALERT_LINKS = ('<{nle_link}|{service}> <{{nle.hermes_url}}|Hermes> '
               '<{{nle.gracedb_url}}|GraceDB> <{{nle.treasuremap_url}}|Treasure Map>')

ALERT_TEXT_RETRACTION = "{{most_likely_class}} {{nle.event_id}} {{nle.state}}"

ALERT_TEXT_BURST = ALERT_TEXT_INTRO + """50% Area = {{seq.localization.area_50:.0f}} deg²
90% Area = {{seq.localization.area_90:.0f}} deg²
Duration = {{duration_ms:.0f}} ms
Frequency = {{central_frequency:.0f}} Hz
""" + ALERT_LINKS

ALERT_TEXT = [  # index = number of localizations available
    ALERT_TEXT_INTRO + ALERT_TEXT_CLASSIFICATION + ALERT_LINKS,
    ALERT_TEXT_INTRO + ALERT_TEXT_LOCALIZATION + ALERT_TEXT_CLASSIFICATION + ALERT_LINKS,
    ALERT_TEXT_INTRO + ALERT_TEXT_LOCALIZATION + ALERT_TEXT_EXTERNAL_COINCIDENCE + ALERT_TEXT_CLASSIFICATION +
    ALERT_LINKS,
]


def send_text(body, is_test_alert=False, is_significant=True, is_burst=False, has_ns=True):
    """This doesn't currently work"""
    body_ascii = body.replace('±', '+/-').replace('²', '2')
    for user in Profile.objects.all():
        if is_test_alert:
            subscribed = user.test_alerts
        elif not is_significant:
            subscribed = user.subthreshold_alerts
        elif is_burst:
            subscribed = user.burst_alerts
        elif has_ns:
            subscribed = user.ns_alerts
        else:
            subscribed = user.bbh_alerts
        if subscribed and user.phone_number is not None:
            twilio_client.messages.create(body=body_ascii, from_=settings.ALERT_SMS_FROM, to=user.phone_number.as_e164)


def send_slack(body, format_kwargs, is_test_alert=False, is_significant=True, is_burst=False, has_ns=True,
               all_workspaces=True, at=None):
    if is_test_alert:
        return
    elif not is_significant:
        channel = 0
    elif is_burst:
        channel = 1
    elif not has_ns:
        channel = 2
    else:
        channel = 3
    if at is not None:
        body = f'<!{at}>\n' + body
    headers = {'Content-Type': 'application/json'}
    for url_list, (nle_link, service), (target_link, _) in zip(settings.SLACK_URLS, settings.NLE_LINKS, settings.TARGET_LINKS):
        body_slack = body.format(nle_link=nle_link, service=service, target_link=target_link).format(**format_kwargs)
        logger.info(f'Sending GW alert: {body_slack}')
        json_data = json.dumps({'text': body_slack})
        requests.post(url_list[channel], data=json_data.encode('ascii'), headers=headers)
        if not all_workspaces:
            break


def send_email(subject, body, is_test_alert=False):
    """This doesn't currently work"""
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = settings.ALERT_EMAIL_FROM
    group = Group.objects.get(name='Test Email Alerts') if is_test_alert else Group.objects.get(name='Email Alerts')
    msg['To'] = ','.join([u.email.split(',')[0] for u in group.user_set.all()])
    if not msg['To']:
        logger.info(f'Email "{subject}" not sent. No one is subscribed.')
        return
    email_text = msg.as_string()

    try:
        server = smtplib.SMTP()
        server.connect()
        server.sendmail(msg['From'], msg['To'], email_text)
        server.close()
        logger.info(f'Email "{subject}" sent!')
    except Exception as e:
        logger.error(f'Email "{subject}" failed: {e}')


def calculate_credible_region(skymap, localization, probability=0.9):
    """store the credible region contour for skymap plotting"""
    # Sort the pixels of the sky map by descending probability density
    skymap.sort('PROBDENSITY', reverse=True)
    # Find the area of each pixel
    skymap['level'], skymap['ipix'] = ah.uniq_to_level_ipix(skymap['UNIQ'])
    pixel_area = ah.nside_to_pixel_area(ah.level_to_nside(skymap['level']))
    # Calculate the probability within each pixel: the pixel area times the probability density
    prob = pixel_area * skymap['PROBDENSITY']
    # Calculate the cumulative sum of the probability
    cumprob = np.cumsum(prob)
    # Find the pixel for which the probability sums to 0.9
    index_90 = cumprob.searchsorted(probability)
    # Find the pixels included in this sum
    skymap90 = skymap[:index_90].group_by('level')
    credible_region_90 = {str(group['level'][0]): [ipix.item() for ipix in group['ipix']] for group in skymap90.groups}
    credible_region_90.setdefault(str(skymap.meta['MOCORDER']), [])  # must include the highest order
    # Create the CredibleRegionContour object
    CredibleRegionContour(localization=localization, probability=probability, pixels=credible_region_90).save()
    logger.info('Calculated skymap contours')


def pick_slack_channel(seq):
    is_test_alert = seq.nonlocalizedevent.event_id.startswith('M')
    is_significant = seq.details['significant']
    is_burst = seq.details['group'] == 'Burst'
    has_ns = seq.details['properties'].get('HasNS', 0.) >= 0.01 \
             or seq.details['classification'].get('BNS', 0.) >= 0.01 \
             or seq.details['classification'].get('NSBH', 0.) >= 0.01
    return is_test_alert, is_significant, is_burst, has_ns


def prepare_and_send_alerts(nle, seq):
    localizations = []
    try:
        if seq is None:  # retraction
            seq = nle.sequences.last()
        else:
            if seq.localization is not None:
                localizations.append(seq.localization)
            if seq.external_coincidence is not None and seq.external_coincidence.localization is not None:
                localizations.append(seq.external_coincidence.localization)
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        format_kwargs = {
            'nle': nle,
            'seq': seq,
            'most_likely_class': get_most_likely_class(seq.details),
            'inverse_far': format_inverse_far(seq.details['far']),
            'significance': 'significant' if is_significant else 'subthreshold',
        }
        if nle.state == 'RETRACTED':
            alert_text = ALERT_TEXT_RETRACTION
        elif is_burst:
            alert_text = ALERT_TEXT_BURST
            format_kwargs['duration_ms'] = seq.details['duration'] * 1000.
        else:
            alert_text = ALERT_TEXT[len(localizations)]
            if localizations:
                format_kwargs['distance'] = format_distance(localizations[0])
            if len(localizations) > 1:
                format_kwargs['distance_external'] = format_distance(localizations[1])
        format_kwargs.update(seq.details['properties'])
        format_kwargs.update(seq.details['classification'])
        format_kwargs.update(seq.details)
    except Exception as e:
        logger.error(f'Could not parse GW alert: {e}')
        alert_text = f'Received a GW alert that could not be parsed. Check GraceDB: {nle.gracedb_url}'
        format_kwargs = {}
        is_test_alert = nle.event_id.startswith('M')
        is_significant = False
        is_burst = False
        has_ns = False
    if is_significant and not is_burst and has_ns:
        at = 'here' if 'RETRACTED' in alert_text else 'channel'
    else:
        at = None
    send_slack(alert_text, format_kwargs,
               is_test_alert=is_test_alert, is_significant=is_significant, is_burst=is_burst, has_ns=has_ns, at=at)
    return localizations


def handle_message_and_send_alerts(message, metadata):
    # get skymap bytes out for later
    try:
        event = message.content[0]['event']
        skymap_bytes = None if event is None else event.get('skymap')
    except Exception as e:  # no matter what, do not crash the listener before ingesting the alert
        logger.error(f'Could not extract skymap from alert: {e}')
        skymap_bytes = None

    # ingest NonLocalizedEvent into the TOM database
    nle, seq = handle_igwn_message(message, metadata)

    if nle is None:  # test event and SAVE_TEST_ALERTS = False
        logger.info('Test alert not saved')
        return

    localizations = prepare_and_send_alerts(nle, seq)

    for localization in localizations:
        if CredibleRegionContour.objects.filter(localization=localization).exists():
            logger.info(f'Localization {localization.id} already exists')
        else:
            update_all_credible_region_percents_for_survey_fields(localization)
            if skymap_bytes is not None:
                skymap = Table.read(BytesIO(skymap_bytes))
                calculate_credible_region(skymap, localization)
                calculate_footprint_probabilities(skymap, localization)

    logger.info(f'Finished processing alert for {nle.event_id}')


def handle_einstein_probe_alert(message, metadata):
    alert = message.content[0]
    logger.warning(f"Handling Einstein Probe alert: {alert}")

    nonlocalizedevent, nle_created = NonLocalizedEvent.objects.get_or_create(
        event_id=alert['id'][0],
        event_type=NonLocalizedEvent.NonLocalizedEventType.UNKNOWN,
    )
    if nle_created:
        logger.info(f"Ingested a new x-ray event with id {nonlocalizedevent.event_id} from EP alert stream")

    # create the localization from ra, dec, radius
    try:
        localization = create_elliptical_localization(
            nonlocalizedevent=nonlocalizedevent,
            center=[alert.get('ra'), alert.get('dec')], radius=alert.get('ra_dec_error'),
        )
    except Exception as e:
        localization = None
        logger.error(f'Could not create EventLocalization for event: {nonlocalizedevent.event_id}. Exception: {e}')
        logger.error(traceback.format_exc())

    logger.debug(f"Storing EP alert: {alert}")

    # Now ingest the sequence for that event
    event_sequence, es_created = EventSequence.objects.update_or_create(
        nonlocalizedevent=nonlocalizedevent,
        localization=localization,
        sequence_id=nonlocalizedevent.sequences.count() + 1,
        details={key: alert.get(key) for key in
                 ['trigger_time', 'image_energy_range', 'net_count_rate', 'image_snr', 'additional_info']},
        event_subtype=alert.get('instrument'),
        ingestor_source='hop',
    )
    if es_created and localization is None:
        warning_msg = (
            f'{"Creating" if es_created else "Updating"} EventSequence without EventLocalization:'
            f'{event_sequence} for NonLocalizedEvent: {nonlocalizedevent}'
        )
        logger.warning(warning_msg)

    return nonlocalizedevent, event_sequence
