from django import template

register = template.Library()


@register.filter
def thumbnail_url(candidate):
    """Returns the base (without _img.png, _ref.png, _diff.png, _scorr.png) of the thumbnail URL"""
    visit = candidate.filename.split('_')[4]
    url = 'http://beast.as.arizona.edu:5013/api/png/'
    url += f'{candidate.obsdate.strftime("%Y/%m/%d")}/{candidate.field}/{candidate.candidatenumber}_{visit}'
    return url
