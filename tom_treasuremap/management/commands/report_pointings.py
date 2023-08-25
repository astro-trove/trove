from django.core.management.base import BaseCommand
from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_surveys.models import SurveyObservationRecord
from ...reporting import report_to_treasure_map
from custom_code.filters import LocalizationFilter
from astropy.time import Time, TimezoneInfo
from astropy import units as u
import logging


logger = logging.getLogger(__name__)


class Command(BaseCommand):

    help = 'Reports completed survey pointings to the Gravitational Wave Treasure Map (https://treasuremap.space)'

    def add_arguments(self, parser):
        parser.add_argument('--lookback-days-nle', help='Nonlocalized events are considered active for this many days',
                            type=float, default=3.)
        parser.add_argument('--lookback-days-obs', help='Report pointings logged less than this many days ago',
                            type=float, default=1.)
        parser.add_argument('--contours-percent', help='Report pointings centered within this localization contour',
                            type=float, default=95.)
        parser.add_argument('--instrument-id', help='The Treasure Map instrument ID for the survey facility',
                            type=int, default=11)

    def handle(self, lookback_days_nle=3., lookback_days_obs=1., contour_percent=95., instrument_id=11):
        now = Time.now()
        lookback_window_nle = now - (lookback_days_nle + lookback_days_obs) * u.day
        active_nles = NonLocalizedEvent.objects.filter(sequences__details__time__gte=lookback_window_nle.isot,
                                                       sequences__details__significant=True,
                                                       event_id__startswith='S').distinct()
        if not active_nles.exists():
            logger.info('No active nonlocalized events found')
            return
        logger.info(f'Found active nonlocalized events: {", ".join([nle.event_id for nle in active_nles])}')

        lookback_window_obs = now - lookback_days_obs * u.day
        recent_obs = SurveyObservationRecord.objects.filter(created__gt=lookback_window_obs.to_datetime(TimezoneInfo()),
                                                            created__lte=now.to_datetime(TimezoneInfo()))
        loc_filter = LocalizationFilter()
        for nle in active_nles:
            matching_observations = loc_filter.filter(recent_obs, (nle, contour_percent, lookback_days_nle))
            logger.info(f'Found {len(matching_observations)} pointings matching {nle.event_id}')
            report_to_treasure_map(matching_observations, nle)
