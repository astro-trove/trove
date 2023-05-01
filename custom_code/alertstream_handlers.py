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

logger = logging.getLogger(__name__)

twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

ALERT_TEXT = """{search} {seq.event_subtype} v{seq.sequence_id}
{nle.event_id} ({significance})
{time}
1/FAR = {inv_far}yr
Dist = {seq.localization.distance_mean:.0f} ± {seq.localization.distance_std:.0f} Mpc
50% Area = {seq.localization.area_50:.0f}°
90% Area = {seq.localization.area_90:.0f}°
Has NS = {HasNS:.0%}
Has Mass Gap = {HasMassGap:.0%}
Has Remnant = {HasRemnant:.0%}
BNS = {BNS:.0%}
NSBH = {NSBH:.0%}
BBH = {BBH:.0%}
Terrestrial = {Terrestrial:.0%}
https://sand.as.arizona.edu/saguaro_tom/nonlocalizedevents/{nle.id}/"""


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
    payload = {'text': body}
    json_data = json.dumps(payload)
    headers = {'Content-Type': 'application/json'}
    for url in settings.SLACK_URLS:
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


def handle_message_and_send_alerts(message, metadata):
    event_objects = handle_igwn_message(message, metadata)
    if event_objects is not None:
        nle, seq = event_objects

    try:
        significance = 'significant' if seq.details['significant'] else 'subthreshold'
        inv_far = format_si_prefix(3.168808781402895e-08 / seq.details['far'])  # 1/Hz to yr
        email_subject = nle.event_id
        body = ALERT_TEXT.format(significance=significance, inv_far=inv_far, nle=nle, seq=seq,
                                 **seq.details, **seq.details['properties'], **seq.details['classification'])
        logger.info(f'Sending GW alert: {body}')
    except Exception as e:
        logger.error(f'Could not parse GW alert: {e}')
        email_subject = 'Unknown GW Alert'
        body = 'Received a GW alert that could not be parsed. Check GraceDB: '
        body += 'https://gracedb.ligo.org/latest/?query=MDC&query_type=S'
    send_text(body)
    send_slack(body)
    send_email(email_subject, body)
