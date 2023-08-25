from django.conf import settings
from .models import TreasureMapPointing
import requests
import logging

TREASUREMAP_POINTINGS_URL = 'https://treasuremap.space/api/v1/pointings'
TREASUREMAP_INSTRUMENT_IDS = {'CSS': 11}

logger = logging.getLogger(__name__)


def report_to_treasure_map(observation_records, nle):
    new_observations = []
    json_pointings = []
    for sor in observation_records:
        tmps = TreasureMapPointing.objects.filter(nonlocalizedevent=nle, observation_record=sor, status=sor.status)
        if tmps.exists():
            logger.info(f'{sor} has already been reported as {sor.status}.')
            continue
        pointing = {
            'ra': sor.survey_field.ra,
            'dec': sor.survey_field.dec,
            'pos_angle': sor.parameters.get('pos_angle', 0.),
            'instrumentid': TREASUREMAP_INSTRUMENT_IDS.get(sor.facility),
            'time': sor.scheduled_start.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'planned' if sor.status == 'PENDING' else sor.status.lower(),  # convert to TM terminology
            'depth': sor.parameters.get('depth', 20.5),
            'depth_unit': sor.parameters.get('depth_unit', 'ab_mag'),
            'band': sor.parameters.get('band', 'open'),
        }
        new_observations.append(sor)
        json_pointings.append(pointing)

    json_data = {'api_token': settings.TREASUREMAP_API_KEY, 'graceid': nle.event_id, 'pointings': json_pointings}
    response = requests.post(url=TREASUREMAP_POINTINGS_URL, json=json_data)
    if response.ok:
        response_json = response.json()
        submitted_pointings = response_json['pointing_ids']
        for sor, treasuremap_id in zip(new_observations, submitted_pointings):
            TreasureMapPointing.objects.create(treasuremap_id=treasuremap_id, nonlocalizedevent=nle,
                                               observation_record=sor, status=sor.status)
        success = f'Submitted {len(submitted_pointings):d} pointings for {nle.event_id} to the Treasure Map'
        logger.info(success)
        response_json['SUCCESSES'] = [success]
        for error in response_json['ERRORS']:
            logger.error(error)
        for warning in response_json['WARNINGS']:
            logger.warning(warning)
    else:
        logger.error(response.text)
        response_json = {'ERRORS': [response.text]}
    return response_json
