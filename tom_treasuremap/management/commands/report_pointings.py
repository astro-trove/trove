from django.core.management.base import BaseCommand
from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_surveys.models import SurveyObservationRecord
from ...reporting import report_to_treasure_map
from custom_code.filters import LocalizationFilter
from astropy.time import Time, TimezoneInfo
from astropy import units as u
import logging


logger = logging.getLogger(__name__)


def get_active_nonlocalizedevents(t0=None, lookback_days=3., test=False):
    """
    Returns a queryset containing "active" NonLocalizedEvents, significant events that happened less than
    `lookback_days` before `t0` and have not been retracted. Use `test=True` to query mock events instead of real ones.
    """
    if t0 is None:
        t0 = Time.now()
    lookback_window_nle = (t0 - lookback_days * u.day).isot
    active_nles = NonLocalizedEvent.objects.filter(sequences__details__time__gte=lookback_window_nle, state='ACTIVE')
    active_nles = active_nles.exclude(sequences__details__significant=False)
    if test:
        active_nles = active_nles.filter(event_id__startswith='MS')
    else:
        active_nles = active_nles.exclude(event_id__startswith='MS')
    return active_nles.distinct()


class Command(BaseCommand):

    help = 'Reports completed survey pointings to the Gravitational Wave Treasure Map (https://treasuremap.space)'

    def add_arguments(self, parser):
        parser.add_argument('--lookback-days-nle', help='Nonlocalized events are considered active for this many days',
                            type=float, default=3.)
        parser.add_argument('--lookback-days-obs', help='Report pointings logged less than this many days ago',
                            type=float, default=1.)
        parser.add_argument('--contours-percent', help='Report pointings centered within this localization contour',
                            type=float, default=95.)
        parser.add_argument('--test', action='store_true', help='Report pointings for test events only')

    def handle(self, lookback_days_nle=3., lookback_days_obs=1., contour_percent=95., test=False, **kwargs):
        now = Time.now()
        lookback_window_obs = now - lookback_days_obs * u.day
        active_nles = get_active_nonlocalizedevents(lookback_window_obs, lookback_days_nle, test=test)
        active_nles = active_nles.filter(event_type=NonLocalizedEvent.NonLocalizedEventType.GRAVITATIONAL_WAVE)
        if not active_nles.exists():
            logger.info('No active GW events found')
            return
        logger.info(f'Found active GW events: {", ".join([nle.event_id for nle in active_nles])}')

        recent_obs = SurveyObservationRecord.objects.filter(created__gt=lookback_window_obs.to_datetime(TimezoneInfo()),
                                                            created__lte=now.to_datetime(TimezoneInfo()))
        loc_filter = LocalizationFilter()
        for nle in active_nles:
            matching_observations = loc_filter.filter(recent_obs, (nle, contour_percent, lookback_days_nle))
            logger.info(f'Found {len(matching_observations)} pointings matching {nle.event_id}')
            if matching_observations.exists():
                report_to_treasure_map(matching_observations, nle)
