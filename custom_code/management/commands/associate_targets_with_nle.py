import numpy as np
from datetime import datetime, timedelta
import logging

from django.core.management.base import BaseCommand
from django.conf import settings

from tom_nonlocalizedevents.models import NonLocalizedEvent 

from trove_targets.models import Target
from custom_code.healpix_utils import (
    get_target_ids_in_prob_credible_region,
    create_candidates_from_targets,
    )

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
            default=settings.SKYMAP_PROB_CONTOUR,
        )
        parser.add_argument(
            "--first-det-tmin",
            help="Associate TNS targets with first detection at EARLIEST "+
            "|first-det-min| days before nonlocalized event. NEGATIVE number "+
            "expected. Default -1, which will consider targets with first "+
            "detection at earliest 1 day before nonlocalized event.",
            type=float,
            default=-1,
        )
        parser.add_argument(
            "--first-det-tmax",
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
               first_det_tmin=-1,
               first_det_tmax=10,
               snr_min=5, 
               **kwargs):
        
        # get the specific NLE and NLE time
        nle = NonLocalizedEvent.objects.filter(id=nle_id)[0]
        seq = nle.sequences.last()
        try:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")
        logger.info(f"\nNLE is {nle}, NLE sequence time is {nle_time}")

        # get targets within the localization region
        logger.info("Getting targets in the "+
                    f"{settings.SKYMAP_PROB_CONTOUR*100:.0f}% localization "+
                    f"region of {nle.event_id}")
        tids = get_target_ids_in_prob_credible_region(
            seq,
            prob=settings.SKYMAP_PROB_CONTOUR,
            tdelta=first_det_tmin)
        tids_ls = list(tids)
        tids_ls = [tid[0] for tid in tids_ls]
        targets = Target.objects.filter(id__in=tids_ls,
                                        created__gte=nle_time+timedelta(first_det_tmin)).order_by("name")
        logger.info(f"Found {len(targets)} targets")

        # loop through targets
        new_candidates = []
        for target in targets:
            logger.info(f"\n{target.name}")
            
            # if excluding based on first detection's SNR...
            # ...is first detection >= SNR minimum?
            if snr_min > 0:
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
            else:
                first_det = target.reduceddatum_set.filter(
                    data_type="photometry",
                    value__magnitude__isnull=False,
                    value__error__isnull=False).order_by("timestamp").first()

            # is first detection within prescribed time window?
            if not(
                first_det.timestamp > nle_time + timedelta(days=first_det_tmin)  and
                first_det.timestamp < nle_time + timedelta(days=first_det_tmax)
                ):
                logger.info("First detection is outside of "+
                            f"{nle_time + timedelta(days=first_det_tmin)} to "+
                            f"{nle_time + timedelta(days=first_det_tmax)} "+
                            "time window")
                continue
            else:
                logger.info("First detection is within "+
                            f"{nle_time + timedelta(days=first_det_tmin)} to "+
                            f"{nle_time + timedelta(days=first_det_tmax)} "+
                            "time window")
                # attempt to create the eventcandidate
                new_cand = create_candidates_from_targets(
                    seq,
                    prob=settings.SKYMAP_PROB_CONTOUR,
                    target_ids=[target.id])
                if len(new_cand):
                    logger.info("New eventcandidate created")
                    new_candidates.append(new_cand)
                else:
                    logger.info("Eventcandidate already exists")
                    
        logger.info(f"\nLinked {len(new_candidates)} candidates to event {nle.event_id}")
        
        return

