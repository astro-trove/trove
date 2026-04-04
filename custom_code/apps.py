from django.apps import AppConfig

class TroveAppConfig(AppConfig):

    name = 'trove'
    
    def nav_items(self):
        """
        Integration point for adding items to the navbar.
        This method should return a list of dictionaries that include a `partial` key pointing to the html templates to
        be included in the navbar. An optional `context` key may be included that should point to the dot separated
        string path to the templatetag that will return a dictionary containing new context for the accompanying
        partial. The `position` key, if included, should be either "left" or "right" to specify which
        side of the navbar the partial should be included on. If not included, a left side nav item is assumed.  We
        provide examples of both here.
        """
        return [{
            'partial': f'{self.name}/partials/navbar.html',
            'position': 'right'
        }]
