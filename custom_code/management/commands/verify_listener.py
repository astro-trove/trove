from django.core.management.base import BaseCommand
from tom_nonlocalizedevents.models import NonLocalizedEvent
from django.conf import settings
from datetime import datetime
import requests
import logging
#from slack_sdk import WebClient


logger = logging.getLogger(__name__)
#slack_client = WebClient(settings.SLACK_TOKENS_GW[0])


class Command(BaseCommand):

    help = 'Verifies that the tom-alertstreams listener is running by checking the latest event in GraceDB'

    def handle(self, **kwargs):
        response = requests.get('https://gracedb.ligo.org/api/superevents')
        response.raise_for_status()
        json_data = response.json()
        events = json_data['superevents']
        latest_event = events[0]['superevent_id']
        nle = NonLocalizedEvent.objects.filter(event_id=latest_event)
        if nle.exists():
            es = nle.last().sequences.last()
            now = datetime.now(es.created.tzinfo)
            dt = (now - es.created).total_seconds()
            message = f'The last GW alert for {latest_event} was received {dt / 3600.:.1f} hours ago'
            logger.info(message)
        else:
            message = (f'The last GW alert for <https://gracedb.ligo.org/superevents/{latest_event}/|{latest_event}> '
                       f'was not received')
            logger.warning(message)
            #slack_client.chat_postMessage(channel='alerts-ns', text=message)
