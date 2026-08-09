from tom_targets.tables import TargetTable
from trove_targets.models import Target

class TroveTargetTable(TargetTable):

    class Meta(TargetTable.Meta):
        model = Target # the model to pull the table info from
        fields = ['selection', 'name', 'ra', 'dec', 'created', ] # the columns in the table
        show_footer = False
