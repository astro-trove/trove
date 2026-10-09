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
from django_tasks import task

from tom_nonlocalizedevents.models import NonLocalizedEvent, EventSequence, EventCandidate
from tom_nonlocalizedevents.alertstream_handlers.igwn_event_handler import handle_igwn_message
from tom_dataproducts.tasks import atlas_query
from tom_antares.antares import AntaresDataService
from tom_targets.utils import cone_search_filter
from tom_targets.models import TargetName

from astropy.table import Table
from astropy.time import Time
import astropy_healpix as ah

from .hooks import (
    target_post_save,
    target_hook_options,
    associate_targets_with_nle
)
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
from scoring.tasks import vet_all_async
from trove_targets.models import Target

import logging
logger = logging.getLogger(__name__)


# for einstein probe
ALERT_TEXT_EP = """Einstein Probe trigger <{target_link}|{{target.name}}>
 — <{nle_link}|Localization>
"""

ALERT_TEXT_INTRO = """{{most_likely_class}} {{seq.event_subtype}} v{{seq.sequence_id}}
{{nle.event_id}} ({{significance}})
{{time}}
1/FAR = {{inverse_far}}
"""

ALERT_TEXT_LOCALIZATION = """Distance = {{distance}}
50% Area = {{area_50}}
90% Area = {{area_90}}
"""

ALERT_TEXT_EXTERNAL_COINCIDENCE = """
Coincident with {{observatory}} {{search}}
GCN Notice {{gcn_notice_id}}
Δt = {{time_difference:.2f}} s
1/FAR (time) = {{inverse_far_time}}
1/FAR (time + pos.) = {{inverse_far_time_pos}}
Distance (comb.) = {{distance_external}}
50% Area (comb.) = {{area_50_external}}
90% Area (comb.) = {{area_90_external}}
"""

ALERT_TEXT_CLASSIFICATION = """Has NS = {{HasNS:.0%}}
Has Mass Gap = {{HasMassGap:.0%}}
Has Remnant = {{HasRemnant:.0%}}
BNS = {{BNS:.0%}}
NSBH = {{NSBH:.0%}}
BBH = {{BBH:.0%}}
Terrestrial = {{Terrestrial:.0%}}
"""

# links for Slack
ALERT_LINKS = (
    "<{nle_link}|{service}> <{{nle.hermes_url}}|Hermes> "
    "<{{nle.gracedb_url}}|GraceDB> <{{nle.treasuremap_url}}|Treasure Map>"
)

ALERT_TEXT_RETRACTION = "{{most_likely_class}} {{nle.event_id}} {{nle.state}}"

ALERT_TEXT_BURST = (
    ALERT_TEXT_INTRO
    + """50% Area = {{seq.localization.area_50:.0f}} deg²
90% Area = {{seq.localization.area_90:.0f}} deg²
Duration = {{duration_ms:.0f}} ms
Frequency = {{central_frequency:.0f}} Hz
"""
    + ALERT_LINKS
)

ALERT_TEXT_SSM = (
    ALERT_TEXT_INTRO
    + ALERT_TEXT_LOCALIZATION
    + """Has NS = {{HasNS:.0%}}
Has Mass Gap = {{HasMassGap:.0%}}
Has SSM = {{HasSSM:.0%}}
"""
    + ALERT_LINKS
)

ALERT_TEXT = [  # index = number of localizations available
    ALERT_TEXT_INTRO + ALERT_TEXT_CLASSIFICATION + ALERT_LINKS,
    ALERT_TEXT_INTRO + ALERT_TEXT_LOCALIZATION + ALERT_TEXT_CLASSIFICATION + ALERT_LINKS,
    ALERT_TEXT_INTRO
    + ALERT_TEXT_LOCALIZATION
    + ALERT_TEXT_CLASSIFICATION
    + ALERT_TEXT_EXTERNAL_COINCIDENCE
    + ALERT_LINKS,
]

# slack_ep = WebClient(settings.SLACK_TOKEN_EP)
# slack_gw = [WebClient(token) for token in settings.SLACK_TOKENS_GW]


def vet_or_post_error(
    target,
    # slack_client,
    # channel,
    **kwargs,
):
    try:
        target.save()  # to do coordinate conversions
        _, tns_query_status = target_post_save(target, created=True, **kwargs)
        if tns_query_status is not None:
            logger.warning(tns_query_status)
            # slack_client.chat_postMessage(channel=channel, text=tns_query_status)

        mjd_now = Time.now().mjd
        atlas_query.enqueue(mjd_now - 20.0, mjd_now, target.id, "atlas_photometry")

    except Exception as e:
        logger.error("".join(traceback.format_exception(e)))
        # slack_client.chat_postMessage(channel=channel, text=f'Error vetting target {target.name}:\n{e}')

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


def pick_slack_channel(seq):
    is_test_alert = seq.nonlocalizedevent.event_id.startswith("M")
    is_significant = seq.details["significant"]
    is_burst = seq.details["group"] == "Burst"
    has_ns = (
        seq.details["properties"].get("HasNS", 0.0) >= 0.01
        or seq.details["classification"].get("BNS", 0.0) >= 0.01
        or seq.details["classification"].get("NSBH", 0.0) >= 0.01
    )
    return is_test_alert, is_significant, is_burst, has_ns


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
        is_test_alert, is_significant, is_burst, has_ns = pick_slack_channel(seq)
        format_kwargs = {
            "nle": nle,
            "seq": seq,
            "most_likely_class": get_most_likely_class(seq.details),
            "inverse_far": format_inverse_far(seq.details["far"]),
            "significance": "significant" if is_significant else "subthreshold",
        }
        format_kwargs.update(seq.details["properties"])
        format_kwargs.update(seq.details["classification"])
        format_kwargs.update(seq.details)
        if nle.state == "RETRACTED":
            alert_text = ALERT_TEXT_RETRACTION
        elif is_burst:
            alert_text = ALERT_TEXT_BURST
            format_kwargs["duration_ms"] = seq.details["duration"] * 1000.0
        else:
            if seq.details["search"] == "SSM":
                alert_text = ALERT_TEXT_SSM
            else:
                alert_text = ALERT_TEXT[len(localizations)]
            if localizations:
                format_kwargs["distance"] = format_distance(localizations[0])
                format_kwargs["area_50"] = format_area(localizations[0].area_50)
                format_kwargs["area_90"] = format_area(localizations[0].area_90)
            if len(localizations) > 1:
                format_kwargs.update(seq.external_coincidence.details)
                format_kwargs["inverse_far_time"] = format_inverse_far(format_kwargs["time_coincidence_far"])
                format_kwargs["inverse_far_time_pos"] = format_inverse_far(
                    format_kwargs["time_sky_position_coincidence_far"]
                )
                format_kwargs["distance_external"] = format_distance(localizations[1])
                format_kwargs["area_50_external"] = format_area(localizations[1].area_50)
                format_kwargs["area_90_external"] = format_area(localizations[1].area_90)
    except Exception as e:
        logger.error(f"Could not parse GW alert: {e}")
        alert_text = f"Received a GW alert that could not be parsed. Check GraceDB: {nle.gracedb_url}"
        format_kwargs = {}
        is_test_alert = nle.event_id.startswith("M")
        is_significant = False
        is_burst = False
        has_ns = False
    if is_significant and not is_burst and has_ns:
        at = "here" if nle.state == "RETRACTED" else "channel"
    else:
        at = None

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
    vet_all_async(stale, nle, vetting_mode)
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

def handle_antares_stream_async(locus, lookback_days_nle=10):
    data_service = AntaresDataService()
    try:
        alert_finite = data_service.serialize_locus(None, locus)
        handle_antares_stream_task.enqueue(alert_finite, lookback_days_nle=lookback_days_nle)
        logger.debug(f"sent {locus.locus_id} to queue")
    except Exception:
        exc = traceback.format_exc()
        dump_alert_and_send_error(alert_finite, exc)


@task(queue_name="antares", priority=settings.PRIORITY_HIGH)
def handle_antares_stream_task(*args, **kwargs):
    """Just a django tasks Task wrapper on handle_antares_stream, this way
    we can still execute the antares alert stream handling serially"""
    handle_antares_stream(*args, **kwargs)
    

def handle_antares_stream(alert, cone_search_radius_arcsec=2.0, lookback_days_nle=10, event_id=None):
    try:
        data_service = AntaresDataService()

        # check for existing targets within 2"
        target_matches = list(
            cone_search_filter(
                Target.objects.all(),
                alert["ra"],
                alert["dec"],
                cone_search_radius_arcsec / 3600.0,
            ).order_by("separation")
        )
        logger.info(
            f"Targets within {cone_search_radius_arcsec:.1f} arcsec: {target_matches}"
        )
        if target_matches:
            # then this target already exists in the Targets table
            target = target_matches[0]
            logger.info(f"Found existing target matching this alert: {target.name}")
            if target.name.startswith('J'):
                target.name = alert['name']
                logger.info(f" - replacing temporary name with {target.name}")

            # add any new aliases from ANTARES to the existing target
            _add_aliases(target, alert)
            
            # then create a candidate and vet it since we know that it is associated
            # with the queried NLE
            # vetting includes updating ANTARES photometry and adding host galaxies
            # this is why we don't do any of that above when we find a target match
            target_post_save(
                target,
                created=True,
                lookback_days_nle=True,
                known_associated_nle_id=event_id,
                query_atlas_fp=_should_run_atlas(alert)
            )
            
        else:
            # then this target does not exist, so we create it from scratch
            # data_service.to_target also saves the target, and will execute the
            # target_post_save function as a hook (see the configuration in settings.py)
            with target_hook_options(
                    lookback_days_nle=lookback_days_nle,
                    known_associated_nle_id=event_id,
                    query_atlas_fp=_should_run_atlas(alert)
            ):
                target = data_service.to_target(alert)

            _add_aliases(target, alert)
                
            logger.info(f"No existing target found, added {target.name} as new target")
            
    except Exception:
        exc = traceback.format_exc()
        logger.error(exc)
        dump_alert_and_send_error(alert, exc)

def _add_aliases(target, alert):
    # add any aliases from the alert
    for alias_name in alert.get("aliases", []):
        alias = TargetName(name=alias_name, target=target)
        try:
            alias.save()
        except IntegrityError:
            logger.info(f"{alias} already exists, skipping and not saving")
        
def dump_alert_and_send_error(
    alert, exc, dump_dir="antares-alert-errors"
):
    """
    we don't want this *ever* to crash, just log the error, send it as a slack message, and dump the alert to a json file
    """
    os.makedirs(dump_dir, exist_ok=True)
    dump_path = f"{dump_dir}/{uuid.uuid4()}.json"
    with open(dump_path, "w") as f:
        json.dump(alert, f, indent=4)

def _should_run_atlas(alert, limit=19.7):
    """
    Check if this alert is bright enough to make running ATLAS FP worth it

    The limiting magnitude of ATLAS c and o filters is 19.7
    (https://fallingstar.com/specifications.php)
    """
    mag = sorted(
        alert["reduced_datums"]["photometry"],
        key=lambda x : x["ant_mjd"]
    )[-1]["ant_mag"]
    return mag < limit
