import numpy as np
from datetime import datetime, timedelta
import logging

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models import OuterRef, Subquery

from tom_nonlocalizedevents.models import NonLocalizedEvent
from tom_dataproducts.models import PhotometryReducedDatum

from trove_targets.models import Target
from trove_nonlocalizedevents.healpix_utils import (
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
               prob=settings.SKYMAP_PROB_CONTOUR,
               first_det_tmin=-1,
               first_det_tmax=10,
               snr_min=5, 
               **kwargs):
        
        # get the specific NLE and NLE time, detection window
        nle = NonLocalizedEvent.objects.filter(id=nle_id)[0]
        seq = nle.sequences.last()
        try:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            nle_time = datetime.strptime(seq.details["time"], "%Y-%m-%dT%H:%M:%S.%f")
        logger.info(f"\nNLE is {nle}, NLE sequence time is {nle_time}")

        det_window_start = nle_time + timedelta(days=first_det_tmin)
        det_window_end   = nle_time + timedelta(days=first_det_tmax)

        # get targets within the localization region, created after det_window_start,
        # with first detection within [det_window_start, det_window_end], and
        # (optionally) with first detection SNR above some threshold
        logger_msg = (
            f"Getting targets in the {prob*100:.0f}% localization region of "+
            f"{nle.event_id}, created after {det_window_start}, with first "+
            f"detection within the [{det_window_start}, {det_window_end}] window"
        )
        if snr_min > 0:
            logger_msg += f" and with first detection SNR > {snr_min}"
        logger_msg += "\n"
        logger.info(logger_msg)
        tids = get_target_ids_in_prob_credible_region(
            seq,
            prob=prob,
            tdelta=first_det_tmin)
        tids_ls = [tid[0] for tid in list(tids)]

        # query for non-zero mags/mag errors
        phot_qs = PhotometryReducedDatum.objects.filter(
            target=OuterRef("pk"),
            brightness__isnull=False,
            brightness_error__isnull=False,
        )
        # add on filter for SNR
        if snr_min > 0:
            brightness_error_max = 2.5/np.log(10)/snr_min
            phot_qs = phot_qs.filter(brightness_error__lte=brightness_error_max)
        # subquery for getting the first detection
        first_det_subquery = Subquery(
            phot_qs.order_by("timestamp").values("timestamp")[:1]
        )
        # combine all the queries
        targets = (
            Target.objects
            .filter(
                id__in=tids_ls,
            )
            .annotate(first_qualifying_det=first_det_subquery)
            .filter(
                first_qualifying_det__gt=det_window_start,
                first_qualifying_det__lt=det_window_end,
            )
            .order_by("name")
        )
        target_ids = targets.values_list("id", flate=True)
        logger.info(f"Found {len(target_ids)} targets")

        # loop through targets
        new_candidates = create_candidates_from_targets(
            seq,
            prob=prob,
            target_ids=target_ids
        )                    
        logger.info(f"\nLinked {len(new_candidates)} candidates to event {nle.event_id}")
        
        return

