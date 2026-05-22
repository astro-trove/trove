import numpy as np
from datetime import datetime, timedelta
import logging

from django.core.management.base import BaseCommand
from django.conf import settings

from tom_nonlocalizedevents.models import NonLocalizedEvent 

from trove_targets.models import Target
from custom_code.healpix_utils import create_candidates_from_targets
from candidate_vetting.vet_basic import vet_basic

# from astropy.coordinates import SkyCoord
# from healpix_alchemy.constants import HPX

logger = logging.getLogger(__name__)
new_format = logging.Formatter('[%(asctime)s] %(levelname)s : s%(message)s')
for handler in logger.handlers:
    handler.setFormatter(new_format)

class Command(BaseCommand):

    help = ("Associate targets with specific NLE if based on targets' "+ 
            "cumulative probability at position in localization region, "+
            "time of first detection, and SNR of first detection")

    def add_arguments(self, parser):
        parser.add_argument('--nle-id', 
                            help='ID of nonlocalized event',
                            type=str)
        parser.add_argument(
            "--prob", 
            help="Target position must have cumulative probability exceeding "+
            "this value in NLE localization region",
            type=float, 
            default=0.95,)
        parser.add_argument(
            "--first-det-min",
            help="Associate TNS targets with first detection at EARLIEST "+
            "|first-det-min| days before nonlocalized event. NEGATIVE number "+
            "expected. Default -1, which will consider targets with first "+
            "detection at earliest 1 day before nonlocalized event.",
            type=float,
            default=-1,
            )
        parser.add_argument(
            "--first-det-max",
            help="Associate TNS targets with first detection at LATEST "+
            "first-det-max days after nonlocalized event. POSITIVE number "+
            "expected.",
            type=float,
            default=10,
        )
        parser.add_argument(
            "--snr-min", 
            help="Targets' first detection must have SNR greater than or "+
            "equal to this value",
            default=5,
            )

    def handle(self, 
               nle_id, 
               prob=0.95, 
               first_det_min=-1,
               first_det_max=10,
               snr_min=5, 
               **kwargs):
        
        # get the specific NLE and NLE time
        nle = NonLocalizedEvent.objects.filter(id=nle_id)[0]
        seq = nle.sequences.last()
        nle_time = datetime.strptime(seq.details['time'], '%Y-%m-%dT%H:%M:%S.%f%z')
        
        # get all targets created after the NLE time
        targets = None ### TODO

        # loop through targets
        new_candidates = []
        for target in targets:
            logger.info(f'\nChecking target {target.name}...')
            # if first detection was >= SNR threshold and within time window
            # attempt to create new eventcandidate associated with NLE
            first_det = target.reduceddatum_set.filter(
                data_type='photometry', 
                value__magnitude__isnull=False,
                value__error__isnull=False,
                value__error__lte=2.5/np.log(10)/snr_min).order_by('timestamp').first()
            if first_det:
                logger.info(f'First non-limit, >= 5-sigma detection: {first_det.timestamp}')
            if (
                nle_time + timedelta(days=first_det_min) < first_det.timestamp and 
                first_det.timestamp < nle_time + timedelta(days=first_det_max)
                ):
                # this will yield a new candidate if the target is within the 
                # `prob` probability region
                new_candidate = create_candidates_from_targets(
                    seq, 
                    prob=prob, 
                    target_ids=[target.id])
                if len(new_candidate):
                    logger.info(f'Target {target.name} was detected within '+
                                f'{t_post} days of the discovery of '+
                                f'{nle.event_id} and lies in its {prob} '+
                                'probability region')
                    new_candidates += new_candidate
                    
                    # do basic vetting, too
                    vet_basic(new_candidate[0].target.id)
                    
        logger.info(f'\nLinked {len(new_candidates)} new candidates to event {nle.event_id}')
        
        return

