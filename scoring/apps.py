from django.apps import AppConfig


class CustomCodeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scoring"

    def target_detail_buttons(self):
        return [
            {
                "namespace": "scoring:vet_form",
                "title": "Run candidate vetting",
                "class": "btn btn-pink",
                "text": "Vet",
                "partial": "scoring/partials/vet_button.html",
            }
        ]
