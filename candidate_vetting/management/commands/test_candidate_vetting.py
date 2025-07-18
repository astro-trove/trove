from django.core.management.base import BaseCommand
from trove_targets.models import Target
from tom_nonlocalizedevents.models import (
    EventCandidate,
    EventLocalization,
    SkymapTile,
    NonLocalizedEvent
)

from astropy.coordinates import SkyCoord

from dataclasses import dataclass
from candidate_vetting.vet_bns import vet_bns

@dataclass
class TestTarget:
    ra:float
    dec:float

class Command(BaseCommand):

    def handle(self, **options):

        target_id = 843837402
        nonlocalized_event_name = "S241109bn" 
        
        vet_bns(target_id, nonlocalized_event_name)
        
        # a harder test case
        #t = TestTarget(11.7132006873, -25.4275854797)
        #host = vet.host_association(t, radius=60*5)
        
        import pdb; pdb.set_trace()
        
