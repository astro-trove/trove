from tom_targets.tables import TargetTable
from trove_targets.models import Target
from tom_common.htmx_table import HTMXTable
import django_tables2 as tables

class TroveTargetTable(HTMXTable):
    
    name = tables.Column(
        linkify=True,
        attrs={"a": {"hx-boost": "false"}}
    )

    class Meta(HTMXTable.Meta):
        model = Target # the model to pull the table info from
        fields = ['name', 'ra', 'dec', 'created'] # the columns in the table
        exclude = ["selection"]        
        template_name = "django_tables2/bootstrap5.html"
        partial_template_name = "trove_targets/partials/target_table_partial.html"
        
