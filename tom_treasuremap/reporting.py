from django.conf import settings
from .models import TreasureMapPointing
import requests
import logging

TREASUREMAP_POINTINGS_URL = 'https://treasuremap.space/api/v1/pointings'
TREASUREMAP_INSTRUMENT_IDS = {'CSS': 11}

logger = logging.getLogger(__name__)


def report_to_treasure_map(observation_records, nle):
    treasuremap_pointings = []
    for sor in observation_records:
        tmps = TreasureMapPointing.objects.filter(nonlocalizedevent=nle, observation_record=sor)
        if tmps.exists():
            tmp = tmps.first()
            if tmp.status == 'PENDING' and sor.status == 'COMPLETED':  # update planned pointing to completed
                tmp.status = sor.status
            else:  # this should never happen
                logger.error(f'There is already a {tmp.status} pointing for {sor.status} {sor}. Not reporting.')
                continue
        else:
            tmp = TreasureMapPointing(nonlocalizedevent=nle, observation_record=sor, status=sor.status)
        treasuremap_pointings.append(tmp)  # don't save them to the db until we know the submission was successful

    json_pointings = []
    for tmp in treasuremap_pointings:
        pointing = {
            'ra': tmp.observation_record.survey_field.ra,
            'dec': tmp.observation_record.survey_field.dec,
            'pos_angle': 0.,  # survey fields have a fixed position angle
            'instrumentid': TREASUREMAP_INSTRUMENT_IDS.get(tmp.observation_record.facility),
            'time': tmp.observation_record.scheduled_start.strftime('%Y-%m-%dT%H:%M:%S'),
            'status': 'planned' if tmp.status == 'PENDING' else tmp.status.lower(),  # convert to TM terminology
            'depth': 21.5,
            'depth_unit': 'ab_mag',
            'band': 'open',
        }
        if tmp.treasuremap_id is not None:
            pointing['id'] = tmp.treasuremap_id
        json_pointings.append(pointing)

    json_data = {'api_token': settings.TREASUREMAP_API_KEY, 'graceid': nle.event_id, 'pointings': json_pointings}
    response = requests.post(url=TREASUREMAP_POINTINGS_URL, json=json_data)
    if response.ok:
        response_json = response.json()
        submitted_pointings = response_json['pointing_ids']
        for tmp, treasuremap_id in zip(treasuremap_pointings, submitted_pointings):
            tmp.treasuremap_id = treasuremap_id
            tmp.save()
        success = f'Submitted {len(treasuremap_pointings):d} pointings for {nle.event_id} to the Treasure Map'
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
