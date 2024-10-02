from django.apps import AppConfig


class CustomCodeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'custom_code'

    def target_detail_buttons(self):
        return {
            'namespace': 'custom_code:vet',
            'title': 'Run kilonova candidate vetting',
            'class': "btn btn-pink",
            'text': 'Vet'
        }
