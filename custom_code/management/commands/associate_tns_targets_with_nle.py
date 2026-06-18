import numpy as np
from datetime import datetime, timedelta
import logging

from django.core.management.base import BaseCommand

from tom_nonlocalizedevents.models import NonLocalizedEvent 

from trove_targets.models import Target
from candidate_vetting.models import TnsQ3C
from custom_code.healpix_utils import create_candidates_from_targets
from custom_code.healpix_utils import get_target_ids_in_prob_credible_region
from candidate_vetting.vet_basic import vet_basic

# from astropy.coordinates import SkyCoord
# from healpix_alchemy.constants import HPX

logger = logging.getLogger(__name__)
new_format = logging.Formatter("[%(asctime)s] %(levelname)s : s%(message)s")
for handler in logger.handlers:
    handler.setFormatter(new_format)

class Command(BaseCommand):
    help = ("Associate targets with specific NLE if based on targets' "+ 
            "cumulative probability at position in localization region, "+
            "time of first detection, and SNR of first detection")


    def add_arguments(self, parser):
        parser.add_argument(
            "--nle-id", 
            help="ID of nonlocalized event",
            type=str,
        )
        parser.add_argument(
            "--prob", 
            help="Target position must have cumulative probability exceeding "+
            "this value in NLE localization region",
            type=float, 
            default=0.95,
        )
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
            "equal to this value. Set to negative number to impose no SNR "+
            "constraint",
            type=float,
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
        nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
        
        logger.info(f"\nNLE is {nle}, NLE sequence time is {nle_time}")

        # get all targets created with discovery date in the relevant time window
        tns_transients = TnsQ3C.objects.filter(
            discoverydate__gte = nle_time + timedelta(days=first_det_min),
            discoverydate__lte = nle_time + timedelta(days=first_det_max)
        )
        tns_transients_ls = list(tns_transients)
        tns_names = [q.name_prefix + q.name for q in tns_transients_ls]
        targets = Target.objects.filter(name__in=tns_names).order_by("name")

        logger.info(f"\nFound {len(targets):d} targets with discovery date "+
                    f"between {first_det_min} and {first_det_max} days of "+
                    f"{nle.event_id} ({nle_time})")
        logger.info(f"{targets}")
        logger.info(f"Now checking if they lie within the {prob} probability "+
                    "region"+f" and their first detection has SNR > {snr_min}." if snr_min > 0 else "")

        ## loop through targets
        new_candidates = []
        for target in targets:
            logger.info(f"\nChecking target {target.name}...")
            
            if snr_min > 0: # if excluding based first detection's SNR and time...
                first_det = target.reduceddatum_set.filter(
                    data_type="photometry",
                    value__magnitude__isnull=False,
                    value__error__isnull=False,
                    value__error__lte=2.5/np.log(10)/snr_min).order_by("timestamp").first()
                # is first detection >= SNR threshold?
                if first_det:
                    logger.info(f"First non-limit, SNR >= {snr_min} detection: {first_det.timestamp}")
                else:
                    logger.info(f"No SNR >= {snr_min} detections, skipping")
                    continue
                # is first detection within prescribed time window?
                if not(
                    first_det.timestamp > nle_time + timedelta(days=first_det_min)  and
                    first_det.timestamp < nle_time + timedelta(days=first_det_max)
                    ):
                    logger.info("First detection is outside of "+
                                f"{nle_time + timedelta(days=first_det_min)} to "+
                                f"{nle_time + timedelta(days=first_det_max)} "+
                                "time window")
                    continue
            # is target within the `prob` credible region?
            tids = get_target_ids_in_prob_credible_region(
                seq,
                prob=prob,
                target_ids=[target.id])
            try:
                _ = [Target(id=tid) for tid in tids][0] # IndexError raised if was not within prob region
                if _:
                    logger.info(f"Target {target.name} lies within {prob} "+
                                f"probability region of {nle.event_id}")
                    # attempt to create the eventcandidate
                    new_cand = create_candidates_from_targets(
                        seq,
                        prob=prob,
                        target_ids=[target.id])
                    if len(new_cand):
                        new_candidates += new_cand
                        logger.info("New eventcandidate created")
                    else:
                        logger.info("Eventcandidate already exists")
            except IndexError:
                logger.info(f"Target {target.name} lies OUTSIDE {prob} "+
                            f"probability region of {nle.event_id}")
                continue
                    
        logger.info(f"\nLinked {len(new_candidates)} candidates to event {nle.event_id}")
        
        return

