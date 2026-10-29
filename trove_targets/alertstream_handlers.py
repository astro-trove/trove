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

from .hooks import (
    target_post_save,
    target_hook_options,
)

from candidate_vetting.vet import localization_sequence_from_name
from scoring.config import DETECTION_HORIZON_DEFAULTS, VETTING_FORM_INITIALS
from scoring.models import ScoreFactor
from scoring.scoring import _localization_from_name
from scoring.tasks import vet_multi_async
from trove_targets.models import Target

import logging
logger = logging.getLogger(__name__)


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

@task(queue_name="antares", priority=settings.PRIORITY_HIGH, backend=settings.TASK_BACKEND)
def handle_antares_stream_task(*args, **kwargs):
    """Just a django tasks Task wrapper on handle_antares_stream, this way
    we can still execute the antares alert stream handling serially"""
    handle_antares_stream(*args, **kwargs)
    

def handle_antares_stream(alert, cone_search_radius_arcsec=2.0, lookback_days_nle=10, event_id=None, verbose=False):
    try:
        data_service = AntaresDataService()

        # json serialize the locus object, if needed
        if isinstance(alert, Locus):
            alert = data_service.serialize_locus(None, alert)
        
        # check for existing targets within 2"
        target_matches = cone_search_filter(
                Target.objects.all(),
                alert["ra"],
                alert["dec"],
                cone_search_radius_arcsec / 3600.0,
            ).order_by("separation")
        if verbose:
            logger.info(
                f"Targets within {cone_search_radius_arcsec:.1f} arcsec: {target_matches.count()}"
            )
        target = target_matches.first()
        if target:
            # add new aliases from the alert
            _add_aliases(target, alert)
            with target_hook_options(skip_vetting=True):
                target.save()
        else:
            # then this target does not exist, so we create it from scratch
            # data_service.to_target also saves the target, and will execute the
            # target_post_save function as a hook (see the configuration in settings.py)
            #
            # BUT, we need to fake it and not actually run any of the vetting yet
            # because we want to wait to update some other fields (e.g., photometry)
            # from the alert
            # updating these fields can only happen *after* the target has been
            # created and saved
            with target_hook_options(skip_vetting=True):
                target = data_service.to_target(alert) # this also adds aliases

        # add new photometry from the alert
        data_service.to_reduced_datums(target, alert["reduced_datums"])
                    
        # now run target post save for real and do the vetting we need to do
        target_post_save(
            target,
            created=True,
            lookback_days_nle=10,
            known_associated_nle_id=event_id,
            query_atlas_fp=_should_run_atlas(alert),
            query_tns_phot=False,
            query_antares_phot=False,
            run_mpc=False # these are filtered out by ANTARES
        )
        
    except Exception:
        exc = traceback.format_exc()
        logger.error(exc)
        dump_alert_and_send_error(alert, exc)
            
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

def _add_aliases(target, alert):
    # add any aliases from the alert
    for alias_name in alert.get("aliases", []):
        alias = TargetName(name=alias_name, target=target)
        try:
            alias.save()
        except IntegrityError:
            logger.info(f"{alias} already exists, skipping and not saving")


def handle_antares_stream_async(locus, lookback_days_nle=10):
    data_service = AntaresDataService()
    try:
        alert_finite = data_service.serialize_locus(None, locus)
        handle_antares_stream_task.enqueue(alert_finite, lookback_days_nle=lookback_days_nle)
        logger.debug(f"sent {locus.locus_id} to queue")
    except Exception:
        exc = traceback.format_exc()
        dump_alert_and_send_error(alert_finite, exc)
