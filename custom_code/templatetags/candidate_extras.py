from django import template
import requests
import base64
import logging

register = template.Library()
logger = logging.getLogger(__name__)


@register.filter
def thumbnail_url(candidate, suffix):
    """Returns an image thumbnail as a data URL"""
    visit = candidate.observation_record.observation_id.split('_')[4]
    url = f'http://sassy.as.arizona.edu/papp/api/{candidate.observation_record.scheduled_start.strftime("%Y/%m/%d")}/'
    url += f'{candidate.observation_record.survey_field}/{candidate.candidatenumber}_{visit}_{suffix}.png'
    return url
    # use the following if the URL is not public
    # try:
    #     response = requests.get(url, timeout=0.2)
    #     return 'data:image/png;base64,' + base64.b64encode(response.content).decode() if response.ok else ''
    # except Exception as e:
    #     logger.error(f'Could not reach url {url}: {e}')
    #     return url
