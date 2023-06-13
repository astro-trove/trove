from tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler import handle_igwn_message
from django.contrib.auth.models import Group
from django.conf import settings
from twilio.rest import Client
from email.mime.text import MIMEText
import requests
import smtplib
import logging
import json
import math
from .healpix_utils import update_all_credible_region_percents_for_css_fields
from .cssfield_selection import calculate_footprint_probabilities, CSS_FOOTPRINT
from .models import CredibleRegionContour
from astropy.table import Table
from io import BytesIO
import astropy_healpix as ah
import numpy as np

logger = logging.getLogger(__name__)

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

ALERT_TEXT_INTRO = """{group} {seq.event_subtype} v{seq.sequence_id}
{nle.event_id} ({significance})
{time}
1/FAR = {inv_far}yr
"""

ALERT_TEXT_LOCALIZATION = """Distance = {seq.localization.distance_mean:.0f} ± {seq.localization.distance_std:.0f} Mpc
50% Area = {seq.localization.area_50:.0f} deg²
90% Area = {seq.localization.area_90:.0f} deg²
"""

ALERT_TEXT_EXTERNAL_COINCIDENCE = "Distance (comb.) = {seq.external_coincidence.localization.distance_mean:.0f} ± " \
                                  """{seq.external_coincidence.localization.distance_std:.0f} Mpc
50% Area (comb.) = {seq.external_coincidence.localization.area_50:.0f} deg²
90% Area (comb.) = {seq.external_coincidence.localization.area_90:.0f} deg²
"""

ALERT_TEXT_CLASSIFICATION = """Has NS = {HasNS:.0%}
Has Mass Gap = {HasMassGap:.0%}
Has Remnant = {HasRemnant:.0%}
BNS = {BNS:.0%}
NSBH = {NSBH:.0%}
BBH = {BBH:.0%}
Terrestrial = {Terrestrial:.0%}
"""

ALERT_TEXT_BURST = """50% Area = {seq.localization.area_50:.0f} deg²
90% Area = {seq.localization.area_90:.0f} deg²
Duration = {duration:.1e} s
Frequency = {central_frequency:.0f} Hz
"""

ALERT_TEXT_URL = "https://sand.as.arizona.edu/saguaro_tom/nonlocalizedevents/{nle.event_id}/"

ALERT_TEXT = [  # index = number of localizations available
    ALERT_TEXT_INTRO + ALERT_TEXT_CLASSIFICATION + ALERT_TEXT_URL,
    ALERT_TEXT_INTRO + ALERT_TEXT_LOCALIZATION + ALERT_TEXT_CLASSIFICATION + ALERT_TEXT_URL,
    ALERT_TEXT_INTRO + ALERT_TEXT_LOCALIZATION + ALERT_TEXT_EXTERNAL_COINCIDENCE + ALERT_TEXT_CLASSIFICATION +
    ALERT_TEXT_URL,
    ALERT_TEXT_INTRO + ALERT_TEXT_BURST + ALERT_TEXT_URL,
]


def send_text(body, is_test_alert=False):
    group = Group.objects.get(name='Test SMS Alerts') if is_test_alert else Group.objects.get(name='SMS Alerts')
    body_ascii = body.replace('±', '+/-').replace('²', '2')
    for user in group.user_set.all():
        if user.username in settings.ALERT_SMS_TO:
            number = settings.ALERT_SMS_TO[user.username]
            twilio_client.messages.create(body=body_ascii, from_=settings.ALERT_SMS_FROM, to=number)
        else:
            logger.error(f'User {user.username} did not provide their phone number')


def send_slack(body, nle, is_test_alert=False):
    if is_test_alert:
        return
    body = ('<!here>\n' if 'RETRACTED' in body else '<!channel>\n') + body
    headers = {'Content-Type': 'application/json'}
    for url, link in zip(settings.SLACK_URLS, settings.SLACK_LINKS):
        body_slack = body.replace(ALERT_TEXT_URL.format(nle=nle), link.format(nle=nle))
        json_data = json.dumps({'text': body_slack})
        requests.post(url, data=json_data.encode('ascii'), headers=headers)


def send_email(subject, body, is_test_alert=False):
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


def format_si_prefix(qty, d=1):
    log1000 = math.log10(qty) / 3.
    if -0.4 < log1000 < 11.:  # switch to scientific notation if it rounds to 0.0 or is above the largest prefix
        i = int(log1000)
        prefix = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'R', 'Q'][i]
        return f'{qty * 1000. ** -i:.{d}f} {prefix}'
    else:
        return f'{qty:.{d}e} '


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

    # send SMS, Slack, and email alerts
    email_subject = nle.event_id
    localizations = []
    try:
        if seq is None:  # retraction
            seq = nle.sequences.last()
            search = seq.details.get('search', '') if seq is not None else ''  # figure out if it was a test event
            body = f'{search} {nle.event_id} {nle.state}'
            logger.info(f'Sending GW retraction: {body}')
        else:
            if seq.localization is not None:
                localizations.append(seq.localization)
            if seq.external_coincidence is not None and seq.external_coincidence.localization is not None:
                localizations.append(seq.external_coincidence.localization)
            significance = 'significant' if seq.details['significant'] else 'subthreshold'
            inv_far = format_si_prefix(3.168808781402895e-08 / seq.details['far'])  # 1/Hz to yr
            alert_text = ALERT_TEXT[len(localizations)] if seq.details['group'] == 'CBC' else ALERT_TEXT[-1]
            body = alert_text.format(significance=significance, inv_far=inv_far, nle=nle, seq=seq,
                                     **seq.details, **seq.details['properties'], **seq.details['classification'])
            logger.info(f'Sending GW alert: {body}')
    except Exception as e:
        logger.error(f'Could not parse GW alert: {e}')
        body = 'Received a GW alert that could not be parsed. Check GraceDB: '
        body += f'https://gracedb.ligo.org/superevents/{nle.event_id}/view/'
    is_test_alert = nle.event_id.startswith('M')
    send_text(body, is_test_alert=is_test_alert)
    send_slack(body, nle, is_test_alert=is_test_alert)
    send_email(email_subject, body, is_test_alert=is_test_alert)

    for localization in localizations:
        if CredibleRegionContour.objects.filter(localization=localization).exists():
            logger.info(f'Localization {localization.id} already exists')
        else:
            update_all_credible_region_percents_for_css_fields(localization)
            if skymap_bytes is not None:
                skymap = Table.read(BytesIO(skymap_bytes))
                calculate_credible_region(skymap, localization)
                calculate_footprint_probabilities(skymap, localization)
