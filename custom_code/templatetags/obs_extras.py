from django import template

register = template.Library()


@register.filter
def display_obs_type(value):
    """
    This converts SAMPLE_TITLE into Sample Title. Used for display all-caps observation type in the
    tabs as titles.
    """
    title = value.replace('_', ' ')
    if value.isupper():
        title = title.title()
    return title
