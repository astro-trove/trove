from django.apps import AppConfig

class CustomCodeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'candidate_vetting'
    def target_detail_buttons(self):
        return {
            'namespace': 'candidate_vetting:vet_form',
            'title': 'Run kilonova candidate vetting',
            'class': "btn btn-pink",
            'text': 'Vet'
        }
