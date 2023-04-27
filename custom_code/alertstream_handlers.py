from tom_nonlocalizedevents.alertstream_handlers.gw_event_handler import handle_message, extract_fields
from tom_nonlocalizedevents.models import NonLocalizedEvent
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

ALERT_TEXT = """{NOTICE_TYPE} v{SEQUENCE_NUM}
{TRIGGER_NUM}
{DATE}
1/FAR = {INV_FAR}yr
Dist = {DISTMEAN:.0f} ± {DISTSTD:.0f} Mpc
Has NS = {PROB_NS}
Has Remnant = {PROB_REMNANT}
BNS = {PROB_BNS}
NSBH = {PROB_NSBH}
BBH = {PROB_BBH}
Terrestrial = {PROB_TERRES}
https://sand.as.arizona.edu/saguaro_tom/nonlocalizedevents/{TOM_ID}/"""


def send_text(body):
    if body.startswith('TEST'):
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
    if body.startswith('TEST'):
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
    if body.startswith('TEST'):
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


def handle_message_and_send_alerts(message):
    handle_message(message)

    try:
        if not isinstance(message, bytes):
            bytes_message = message.value()
        else:
            bytes_message = message
        fields = ['NOTICE_TYPE', 'TRIGGER_NUM', 'SEQUENCE_NUM', 'FAR',
                  'PROB_BBH', 'PROB_BNS', 'PROB_NSBH', 'PROB_TERRES', 'PROB_NS', 'PROB_REMNANT', 'PROB_MassGap']
        alert = extract_fields(bytes_message.decode('utf-8'), fields)
        for field in fields:
            if field == 'FAR' or field.startswith('PROB'):
                alert[field] = float(alert[field].split(' ')[0])
        alert['INV_FAR'] = format_si_prefix(3.168808781402895e-08 / alert['FAR'])  # 1/Hz to yr

        nle = NonLocalizedEvent.objects.get(event_id=alert['TRIGGER_NUM'])
        seq = nle.sequences.get(sequence_id=alert['SEQUENCE_NUM'])
        alert['TOM_ID'] = nle.id
        if seq.localization is None:
            alert['DATE'] = "Couldn't parse sky map"
            alert['DISTMEAN'] = math.nan
            alert['DISTSTD'] = math.nan
        else:
            alert['DATE'] = seq.localization.date.strftime('%F %T')
            alert['DISTMEAN'] = seq.localization.distance_mean
            alert['DISTSTD'] = seq.localization.distance_std
        email_subject = alert['TRIGGER_NUM']
        body = ALERT_TEXT.format(**alert)
        logger.info(f'Sending GW alert: {body}')
    except Exception as e:
        logger.error(f'Could not parse GW alert: {e}')
        email_subject = 'Unknown GW Alert'
        body = 'Received a GW alert that could not be parsed. Check GraceDB: '
        body += 'https://gracedb.ligo.org/latest/?query=MDC&query_type=S'
    send_text(body)
    send_slack(body)
    send_email(email_subject, body)
