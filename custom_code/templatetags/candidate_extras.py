from django import template
import requests
import base64

register = template.Library()


@register.filter
def thumbnail_url(candidate, suffix):
    """Returns an image thumbnail as a data URL"""
    visit = candidate.filename.split('_')[4]
    url = f'http://beast.as.arizona.edu:5013/api/png/{candidate.obsdate.strftime("%Y/%m/%d")}/'
    url += f'{candidate.field}/{candidate.candidatenumber}_{visit}_{suffix}.png'
    try:
        response = requests.get(url, timeout=0.2)
        return 'data:image/png;base64,' + base64.b64encode(response.content).decode() if response.ok else ''
    except requests.exceptions.ConnectionError:
        return ''
