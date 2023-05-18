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
from .cssfield_selection import get_prob_radec, CSS_FOOTPRINT
from .models import CredibleRegionContour
from ligo.skymap import bayestar
from astropy.table import Table
from io import BytesIO
import healpy
import astropy_healpix as ah
import numpy as np

logger = logging.getLogger(__name__)

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

ALERT_TEXT = """{search} {seq.event_subtype} v{seq.sequence_id}
{nle.event_id} ({significance})
{time}
1/FAR = {inv_far}yr
Distance = {seq.localization.distance_mean:.0f} ± {seq.localization.distance_std:.0f} Mpc
50% Area = {seq.localization.area_50:.0f} deg²
90% Area = {seq.localization.area_90:.0f} deg²
Has NS = {HasNS:.0%}
Has Mass Gap = {HasMassGap:.0%}
Has Remnant = {HasRemnant:.0%}
BNS = {BNS:.0%}
NSBH = {NSBH:.0%}
BBH = {BBH:.0%}
Terrestrial = {Terrestrial:.0%}
https://sand.as.arizona.edu/saguaro_tom/nonlocalizedevents/{nle.event_id}/"""

ALERT_TEXT_NO_LOCALIZATION = ALERT_TEXT[:107] + 'Sky map not ingested\n' + ALERT_TEXT[291:]


def send_text(body):
    if body.startswith('MDC'):
        group = Group.objects.get(name='Test SMS Alerts')
    else:
        group = Group.objects.get(name='SMS Alerts')
    for user in group.user_set.all():
        if user.username in settings.ALERT_SMS_TO:
            number = settings.ALERT_SMS_TO[user.username]
            twilio_client.messages.create(body=body, from_=settings.ALERT_SMS_FROM, to=number)
        else:
            logger.error(f'User {user.username} did not provide their phone number')


def send_slack(body):
    if body.startswith('MDC'):
        return
    lines = body.splitlines()
    lines.insert(0, '<!here>' if 'RETRACTED' in body else '<!channel>')
    headers = {'Content-Type': 'application/json'}
    for url, link in zip(settings.SLACK_URLS, settings.SLACK_LINKS):
        if lines[-1].startswith('http'):
            lines[-1] = link
        json_data = json.dumps({'text': '\n'.join(lines)})
        requests.post(url, data=json_data.encode('ascii'), headers=headers)


def send_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = settings.ALERT_EMAIL_FROM
    if body.startswith('MDC'):
        group = Group.objects.get(name='Test Email Alerts')
    else:
        group = Group.objects.get(name='Email Alerts')
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
    if -1. < log1000 < 11.:
        i = int(log1000)
        prefix = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y', 'R', 'Q'][i]
        return f'{qty * 1000. ** -i:.{d}f} {prefix}'
    else:
        return f'{qty:.{d}e}'


def calculate_credible_region(skymap, localization, probability=0.9):
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


def handle_message_and_send_alerts(message, metadata):
    # get skymap table out for later
    try:
        alert = message.content[0]
        skymap_bytes = alert['event']['skymap']
        skymap = Table.read(BytesIO(skymap_bytes))
    except Exception as e:  # no matter what, do not crash the listener before ingesting the alert
        logger.error(f'Could not extract skymap from alert: {e}')
        skymap = None

    # ingest NonLocalizedEvent into the TOM database
    nle, seq = handle_igwn_message(message, metadata)

    if nle is None:  # test event and SAVE_TEST_ALERTS = False
        logger.info('Test alert not saved')
        return

    # send SMS, Slack, and email alerts
    email_subject = nle.event_id
    try:
        if seq is None:  # retraction
            seq = nle.sequences.last()
            search = seq.details.get('search', '') if seq is not None else ''  # figure out if it was a test event
            body = f'{search} {nle.event_id} {nle.state}'
            logger.info(f'Sending GW retraction: {body}')
        else:
            significance = 'significant' if seq.details['significant'] else 'subthreshold'
            inv_far = format_si_prefix(3.168808781402895e-08 / seq.details['far'])  # 1/Hz to yr
            alert_text = ALERT_TEXT_NO_LOCALIZATION if seq.localization is None else ALERT_TEXT
            body = alert_text.format(significance=significance, inv_far=inv_far, nle=nle, seq=seq,
                                     **seq.details, **seq.details['properties'], **seq.details['classification'])
            logger.info(f'Sending GW alert: {body}')
    except Exception as e:
        logger.error(f'Could not parse GW alert: {e}')
        body = 'Received a GW alert that could not be parsed. Check GraceDB: '
        body += f'https://gracedb.ligo.org/superevents/{nle.event_id}/view/'
    send_text(body)
    send_slack(body)
    send_email(email_subject, body)

    if seq.localization is not None:
        # store the credible region contour for skymap plotting
        calculate_credible_region(skymap, seq.localization)

        # store credible regions for CSS fields (for associating candidates)
        update_all_credible_region_percents_for_css_fields(seq.localization)
        logger.info('Updated credible regions for CSS fields')

        # get the total probability in each CSS field footprint
        flat = bayestar.rasterize(skymap)
        probs = healpy.reorder(flat['PROB'], 'NESTED', 'RING')
        nside = healpy.npix2nside(len(probs))
        get_prob_radec(probs, nside, CSS_FOOTPRINT, seq.localization.css_field_credible_regions.all())
        logger.info('Updated probabilities for CSS fields')
