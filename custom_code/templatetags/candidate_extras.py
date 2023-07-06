from django import template
import requests
import base64
import logging

register = template.Library()
logger = logging.getLogger(__name__)


@register.filter
def thumbnail_url(candidate, suffix):
    """Returns an image thumbnail as a data URL"""
    visit = candidate.filename.split('_')[4]
    url = f'http://sassy.as.arizona.edu/papp/api/{candidate.obsdate.strftime("%Y/%m/%d")}/'
    url += f'{candidate.field}/{candidate.candidatenumber}_{visit}_{suffix}.png'
    try:
        response = requests.get(url, timeout=0.2)
        return 'data:image/png;base64,' + base64.b64encode(response.content).decode() if response.ok else ''
    except Exception as e:
        logger.error(f'Could not reach url {url}: {e}')
        return ''
