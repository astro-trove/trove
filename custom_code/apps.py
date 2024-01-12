from django.apps import AppConfig


class CustomCodeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'custom_code'
    integration_points = {'target_detail_button': {'namespace': 'custom_code:vet',
                                                   'title': 'Run kilonova candidate vetting',
                                                   'class': "btn btn-pink",
                                                   'text': 'Vet'}}
