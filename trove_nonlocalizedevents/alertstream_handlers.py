import time
import os
import uuid
import json
from datetime import datetime
import traceback
from io import BytesIO

import numpy as np

from email.mime.text import MIMEText
# from slack_sdk import WebClient
import smtplib

from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.conf import settings
from django.db.utils import IntegrityError
from django.core.exceptions import FieldError
from django_tasks import task

from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence, EventCandidate
from tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler import handle_igwn_message
from tom_dataproducts.tasks import atlas_query
from tom_antares.antares import AntaresDataService
from tom_targets.utils import cone_search_filter
from tom_targets.models import TargetName

from antares_client.models import Locus

from astropy.table import Table
from astropy.time import Time
import astropy_healpix as ah

from .hooks import associate_targets_with_nle
from .templatetags.nonlocalizedevent_extras import (
    format_inverse_far,
    format_distance,
    format_area,
    get_most_likely_class,
)
from .healpix_utils import create_elliptical_localization
from .models import CredibleRegionContour

from candidate_vetting.vet import localization_sequence_from_name
from scoring.config import DETECTION_HORIZON_DEFAULTS, VETTING_FORM_INITIALS
from scoring.models import ScoreFactor
from scoring.scoring import _localization_from_name
from scoring.tasks import vet_multi_async
from trove_targets.alertstream_handlers import vet_or_post_error
from trove_targets.models import Target

import logging
logger = logging.getLogger(__name__)


# for einstein probe
ALERT_TEXT_EP = """Einstein Probe trigger <{target_link}|{{target.name}}>
 — <{nle_link}|Localization>
"""

def calculate_credible_region(skymap, localization, probability=0.9):
    t0 = time.time()
    """store the credible region contour for skymap plotting"""
    # Sort the pixels of the sky map by descending probability density
    skymap.sort("PROBDENSITY", reverse=True)
    # Find the area of each pixel
    skymap["level"], skymap["ipix"] = ah.uniq_to_level_ipix(skymap["UNIQ"])
    pixel_area = ah.nside_to_pixel_area(ah.level_to_nside(skymap["level"]))
    # Calculate the probability within each pixel: the pixel area times the probability density
    prob = pixel_area * skymap["PROBDENSITY"]
    # Calculate the cumulative sum of the probability
    cumprob = np.cumsum(prob)
    # Find the pixel for which the probability sums to 0.9
    index_90 = cumprob.searchsorted(probability)
    # Find the pixels included in this sum
    skymap90 = skymap[:index_90].group_by("level")
    credible_region_90 = {str(group["level"][0]): [ipix.item() for ipix in group["ipix"]] for group in skymap90.groups}
    if "MOCORDER" in skymap.meta:
        credible_region_90.setdefault(str(skymap.meta["MOCORDER"]), [])  # must include the highest order
    # Create the CredibleRegionContour object
    CredibleRegionContour(localization=localization, probability=probability, pixels=credible_region_90).save()
    dt = time.time() - t0
    logger.info(f"Calculated skymap contours in {dt:.0f} s")

def prepare_and_send_alerts(nle, seq):
    localizations = []
    try:
        if seq is None:  # retraction
            seq = nle.sequences.last()
        else:
            if seq.localization is not None:
                localizations.append(seq.localization)
            if seq.external_coincidence is not None and seq.external_coincidence.localization is not None:
                localizations.append(seq.external_coincidence.localization)
    except Exception as e:
        logger.error(f"Could not parse GW alert: {e}")
    
    return localizations


def _json_dump_default(obj):
    if isinstance(obj, bytes):
        return {"__bytes__": len(obj)}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _message_payload_for_json_dump(message):
    """Return the HOP message content for archival JSON (not the JSONBlob wrapper)."""
    if hasattr(message, "content"):
        return message.content
    return message


def revet_stale_candidates(nle, most_likely_class):
    """Re-vet the candidates whose scores predate the current localization.

    A new skymap moves the credible region and the distance at every target's
    healpix, so a candidate's skymap_score and host_distance_score are only
    valid for the localization they were computed against. The NLE-dependent
    vetting modes record that as a "localization_id" ScoreFactor, so anything
    carrying a different one -- or none at all, meaning it was vetted before we
    started recording it, or has never been vetted -- is stale.

    Candidates already scored against the current localization are left alone,
    which is what stops a sequence that reuses an existing skymap from kicking
    off a pointless full re-vet. It also means this converges: once a candidate
    has been re-vetted, the next alert skips it.

    Returns the number of candidates queued.
    """
    # basic vetting writes no localization-dependent scores at all, so there is
    # nothing for a new skymap to invalidate -- and since it never stamps a
    # localization_id, re-vetting would re-queue the same candidates forever
    vetting_mode = VETTING_FORM_INITIALS.get(
        most_likely_class, VETTING_FORM_INITIALS[""]
    )[0]
    if vetting_mode == "basic":
        logger.info(
            f"{nle.event_id} vets in basic mode, which has no "
            + "localization-dependent scores; not re-vetting"
        )
        return 0

    candidates = EventCandidate.objects.filter(nonlocalizedevent_id=nle.id)
    if not candidates.exists():
        return 0

    # resolve the localization the vetting code itself would use, so the two
    # cannot disagree about what "current" means
    localization = _localization_from_name(nle.event_id)
    if localization is None:
        logger.warning(f"No localization for {nle.event_id}, not re-vetting")
        return 0

    current = ScoreFactor.objects.filter(
        event_candidate__in=candidates,
        key="localization_id",
        value=str(localization.id),
    ).values_list("event_candidate_id", flat=True)

    stale = candidates.exclude(id__in=list(current))
    n_stale = stale.count()
    if not n_stale:
        logger.info(
            f"All {candidates.count()} candidates for {nle.event_id} are "
            + f"already scored against localization {localization.id}"
        )
        return 0

    logger.info(
        f"Re-vetting {n_stale} of {candidates.count()} candidates for "
        + f"{nle.event_id} in {vetting_mode} mode against localization "
        + f"{localization.id} ({localization.date})"
    )
    vet_multi_async(stale, nle, vetting_mode)
    return n_stale


def handle_message_and_send_alerts(message, metadata):
    jname = str(Time.now()).replace(" ", "T") + "-alert.json"
    try:
        with open(jname, "w") as f:
            json.dump(
                _message_payload_for_json_dump(message),
                f,
                default=_json_dump_default,
            )
    except Exception as e:
        logger.error(f"Could not write alert archive {jname}: {e}")

    # because they change the alert format but tom_nonlocalizedevents doesn't expect it
    message.content = [message.content]
    
    # get skymap bytes out for later
    skymaps = []
    try:
        alert = message.content[0]
        event = alert.get("event")
        if event is not None:
            skymaps.append(event.get("skymap"))
        external_coinc = alert.get("external_coinc")
        if external_coinc is not None:
            skymaps.append(external_coinc.get("combined_skymap"))
    except Exception as e:  # no matter what, do not crash the listener before ingesting the alert
        logger.error(f"Could not extract skymap from alert: {e}")

    # ingest NonLocalizedEvent into the TOM database
    nle, seq = handle_igwn_message(message, metadata)

    if nle is None:  # test event and SAVE_TEST_ALERTS = False
        logger.info("Test alert not saved")
        return None, None

    localizations = prepare_and_send_alerts(nle, seq)

    for skymap_bytes, localization in zip(skymaps, localizations):
        if CredibleRegionContour.objects.filter(localization=localization).exists():
            logger.info(f"Localization {localization.id} already exists")
        else:
            if skymap_bytes is not None:
                skymap = Table.read(BytesIO(skymap_bytes))
                calculate_credible_region(skymap, localization)
    
    # check for targets over appropriate time horizon
    nle_eventseq = localization_sequence_from_name(nle.event_id)
    nle_most_likely_class = get_most_likely_class(nle_eventseq.details)  # most likely class for the NLE
    associate_targets_with_nle(
        nle,
        DETECTION_HORIZON_DEFAULTS[nle_most_likely_class][0],
        DETECTION_HORIZON_DEFAULTS[nle_most_likely_class][-1]
    )

    # a new skymap invalidates the scores computed against the old one
    try:
        revet_stale_candidates(nle, nle_most_likely_class)
    except Exception as e:  # never let re-vetting break alert ingestion
        logger.error(f"Could not re-vet candidates for {nle.event_id}: {e}")

    logger.info(f"Finished processing alert for {nle.event_id}")
    return nle, seq


def handle_einstein_probe_alert(message, metadata):
    alert = message.content
    logger.warning(f"Handling Einstein Probe alert: {alert}")

    nonlocalizedevent, nle_created = NonLocalizedEvent.objects.get_or_create(
        event_id=alert["id"][0],
        event_type=NonLocalizedEvent.NonLocalizedEventType.UNKNOWN,
    )
    if nle_created:
        logger.info(f"Ingested a new x-ray event with id {nonlocalizedevent.event_id} from EP alert stream")

    # create the localization from ra, dec, radius
    try:
        localization, skymap = create_elliptical_localization(
            nonlocalizedevent=nonlocalizedevent,
            center=[alert.get("ra"), alert.get("dec")],
            radius=alert.get("ra_dec_error"),
        )
    except Exception as e:
        localization = None
        skymap = None
        logger.error(f"Could not create EventLocalization for event: {nonlocalizedevent.event_id}. Exception: {e}")
        logger.error(traceback.format_exc())

    logger.debug(f"Storing EP alert: {alert}")

    # Now ingest the sequence for that event
    details = {
        key: alert.get(key)
        for key in ["instrument", "image_energy_range", "net_count_rate", "image_snr", "additional_info"]
    }
    details["time"] = alert.get("trigger_time")  # to match IGWN alerts
    event_sequence, es_created = EventSequence.objects.update_or_create(
        nonlocalizedevent=nonlocalizedevent,
        localization=localization,
        sequence_id=nonlocalizedevent.sequences.count() + 1,
        details=details,
    )
    if es_created and localization is None:
        warning_msg = (
            f"{'Creating' if es_created else 'Updating'} EventSequence without EventLocalization:"
            f"{event_sequence} for NonLocalizedEvent: {nonlocalizedevent}"
        )
        logger.warning(warning_msg)

    if CredibleRegionContour.objects.filter(localization=localization).exists():
        logger.info(f"Localization {localization.id} already exists")
    else:
        if skymap is not None:
            skymap["PROBDENSITY"].unit = "1 / sr"
            calculate_credible_region(skymap, localization)

    ep_ra = alert.get("ra")
    ep_dec = alert.get("dec")
    ep_name = alert["id"][0]
    t_ep = Target.objects.create(name=ep_name, type="SIDEREAL", ra=ep_ra, dec=ep_dec, permissions="PUBLIC")
    # Get or create is safer and prevents the 500 crash here
    EventCandidate.objects.get_or_create(target=t_ep, nonlocalizedevent=nonlocalizedevent)
    vet_or_post_error(t_ep)
    query = {"localization_event": nonlocalizedevent.event_id, "localization_prob": 95, "localization_dt": 3}
    survey_obs_link = (
        f"https://{Site.objects.get_current().domain}{reverse('surveys:observations')}?{urllib.parse.urlencode(query)}"
    )
    alert_text = ALERT_TEXT_EP.format(survey_obs_link=survey_obs_link, target_link=settings.TARGET_LINKS[0][0]).format(
        target=t_ep
    )
    logger.info(f"Sending EP alert: {alert_text}")
    # slack_ep.chat_postMessage(channel='alerts-ep', text=alert_text)

    logger.info(f"Finished processing alert for {nonlocalizedevent.event_id}")


def handle_icecube_alert(alert):
    logger.warning(f"Handling Einstein Probe alert: {alert}")

    nonlocalizedevent, nle_created = NonLocalizedEvent.objects.get_or_create(
        event_id=alert["RunNum_EventNum"],
        event_type=NonLocalizedEvent.NonLocalizedEventType.NEUTRINO,
    )
    if nle_created:
        logger.info(f"Ingested a new x-ray event with id {nonlocalizedevent.event_id} from EP alert stream")

    # create the localization from ra, dec, radius
    try:
        localization, skymap = create_elliptical_localization(
            nonlocalizedevent=nonlocalizedevent,
            center=[alert["RA [deg]"], alert["Dec [deg]"]],
            radius=alert["Error90 [arcmin]"] / 60.0,
        )
    except Exception as e:
        localization = None
        skymap = None
        logger.error(f"Could not create EventLocalization for event: {nonlocalizedevent.event_id}. Exception: {e}")
        logger.error(traceback.format_exc())

    logger.debug(f"Storing IceCube alert: {alert}")

    # Now ingest the sequence for that event
    icecube_keys = {"notice_type": "NoticeType", "energy": "Energy", "signalness": "Signalness", "far": "FAR [#/yr]"}
    details = {key: alert[val] for key, val in icecube_keys.items()}
    details["time"] = datetime.strptime(alert["Date"] + alert["Time UT"], "%y/%m/%d%H:%M:%S.%f").isoformat()
    event_sequence, es_created = EventSequence.objects.update_or_create(
        nonlocalizedevent=nonlocalizedevent,
        localization=localization,
        sequence_id=alert["Rev"],
        details=details,
    )
    if es_created and localization is None:
        warning_msg = (
            f"{'Creating' if es_created else 'Updating'} EventSequence without EventLocalization:"
            f"{event_sequence} for NonLocalizedEvent: {nonlocalizedevent}"
        )
        logger.warning(warning_msg)

    if CredibleRegionContour.objects.filter(localization=localization).exists():
        logger.info(f"Localization {localization.id} already exists")
    else:
        if skymap is not None:
            skymap["PROBDENSITY"].unit = "1 / sr"
            calculate_credible_region(skymap, localization)

    logger.info(f"Finished processing alert for {nonlocalizedevent.event_id}")
