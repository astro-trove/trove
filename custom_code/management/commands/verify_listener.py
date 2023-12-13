from django.core.management.base import BaseCommand
from tom_nonlocalizedevents.models import EventSequence
from django.conf import settings
from datetime import datetime
import requests
import logging
import json


logger = logging.getLogger(__name__)


class Command(BaseCommand):

    help = 'Verifies that the tom-alertstreams listener is running by checking the time of the latest event'

    def add_arguments(self, parser):
        parser.add_argument('--max-seconds', help='Longest acceptable time not to receive an alert in seconds',
                            type=float, default=3600.)

    def handle(self, max_seconds=3600., **kwargs):
        es = EventSequence.objects.order_by('created').last()
        now = datetime.now(es.created.tzinfo)
        dt = (now - es.created).total_seconds()
        message = f'The last GW alert was received {dt:.0f} s ago'
        logger.info(message)
        if dt > max_seconds:
            headers = {'Content-Type': 'application/json'}
            data = json.dumps({'text': message}).encode('ascii')
            requests.post(settings.SLACK_URLS[0][-1], data=data, headers=headers)
