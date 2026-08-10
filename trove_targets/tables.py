from tom_targets.tables import TargetTable
from trove_targets.models import Target
from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_common.htmx_table import HTMXTable
import django_tables2 as tables
from django.utils.html import format_html

class _EventIDListColumn(tables.Column):
    def render(self, value):
        if not value:
            return ""
        
        # Split by newline and create links for each
        event_ids = value.strip().split('\n')
        links = []
        
        for event_id in event_ids:
            nle = NonLocalizedEvent.objects.get(event_id=event_id)
            url = f"/eventcandidates/?nonlocalizedevent={nle.id}"  # or use reverse()
            link = format_html(
                '<a href="{}" target="_blank">{}</a>',
                url,
                event_id
            )
            links.append(link)
        
        # Join links with <br>
        return format_html('<br>'.join(str(link) for link in links))

class TroveTargetTable(HTMXTable):
    
    name = tables.Column(
        linkify=True,
        attrs={"a": {"hx-boost": "false"}}
    )
    associated_events = _EventIDListColumn()
    
    class Meta(HTMXTable.Meta):
        model = Target # the model to pull the table info from
        fields = ['name', 'ra', 'dec', 'first_detection', '_z', 'associated_events'] # the columns in the table
        exclude = ["selection"]
        template_name = "django_tables2/bootstrap5.html"
        partial_template_name = "trove_targets/partials/target_table_partial.html"
        
