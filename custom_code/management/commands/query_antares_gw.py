"""
This management command queries the ANTARES broker for loci associated with
"active" GW events in their database
"""
import sys
import argparse
from itertools import islice

from django.core.management.base import BaseCommand
from antares_client.search import search
from elasticsearch_dsl import Search as ES_Search, Q as ES_Q

from tom_antares.antares import AntaresDataService
from tom_nonlocalizedevents.models import EventCandidate
from tom_targets.models import TargetName

from custom_code.alertstream_handlers import (
    handle_antares_stream,
    handle_antares_stream_async
)
from custom_code.hooks import get_active_nonlocalizedevents

import logging
logger = logging.getLogger(__name__)
new_format = logging.Formatter("[%(asctime)s] %(levelname)s : s%(message)s")
for handler in logger.handlers:
    handler.setFormatter(new_format)

def process_loci(
        loci, test_num_alerts=sys.maxsize, async_alert_processing=True,
        lookback_days_nle=10, event_id=None
):
    """
    This processes a list of loci and ingests the data and/or target info into the
    TROVE database
    """
    count = 0
    loci = islice(loci, test_num_alerts)
    for locus in loci:
        if async_alert_processing:
            handle_antares_stream_async(
                locus,
                lookback_days_nle=lookback_days_nle,
                event_id=event_id
            )
        else:
            ds = AntaresDataService()
            alert = ds.serialize_locus(None, locus)
            handle_antares_stream(
                alert,
                lookback_days_nle=lookback_days_nle,
                event_id=event_id
            )
        count += 1
    return count
        
def get_active_gw_events(lookback_days=10):
    """
    This gets a list of active GW events stored in the TROVE database
    """
    active_nles = get_active_nonlocalizedevents(lookback_days=lookback_days)
    active_gw = active_nles.filter(
        event_id__startswith="S"
    ).values_list(
        "event_id",
        flat=True
    )
    return list(active_gw)

def query_for_one_event(event_id: str):
    """
    Query ANTARES for all loci associated with a GraceDB GW event,
    excluding solar system objects (SSOs).

    This query is the same as saying
    (
        grav_wave_events.keyword == event_id AND
        (
            (properties.ztf_ssnamenr in alert AND properties.ztfssnamenr == "null")
            OR
            (properties.lsst_diaSource_ssObjectId in alert AND properties.lsst_diaSource_ssObjectId == 0)
            OR 
            (properties.lsst_diaSource not in alert AND properties.ztf_ssnamenr not in alert)
        )
    )
    """

    # get current ANT* names for the given event_id 
    ant_target_names = get_current_ant_candidate_names(event_id)

    # then build the "No SSO" part of the query
    ztf_not_sso = ES_Q("term", **{"properties.ztf_ssnamenr": "null"})
    lsst_not_sso = ES_Q("term", **{"properties.lsst_diaSource_ssObjectId": 0})
    neither_key = ES_Q(
        "bool",
        must_not=[
            ES_Q("exists", field="properties.ztf_ssnamenr"),
            ES_Q("exists", field="properties.lsst_diaSource_ssObjectId"),
        ],
    )

    # put it all together in an ElasticSearch query dict to give to ANTARES
    query = (
        ES_Search()
        .filter("term", **{"grav_wave_events.keyword": event_id})
        .filter(ztf_not_sso | lsst_not_sso | neither_key)
        .exclude("terms", locus_id=ant_target_names)
        .to_dict()
    )

    # return all of the ANTARES loci associated with this event that we don't already
    # have in the TROVE database
    return search(query)

def get_current_ant_candidate_names(event_id):
    """
    This gets a list of current aliases of targets associated with the passed in NLE
    so that we can exclude them in our query to ANTARES. This will reduce the processing
    runtime!
    """

    # get a list of the event candidate ids
    ec_ids = EventCandidate.objects.filter(
        nonlocalizedevent__event_id=event_id
    ).values_list(
        "target_id",
        flat=True
    )

    # also get a list of target name objects that start with ANT
    ant_target_names = TargetName.objects.filter(
        name__startswith="ANT",
        target_id__in=ec_ids
    ).values_list(
        "name",
        flat=True
    )

    return list(ant_target_names)
    
class Command(BaseCommand):
    help = "Query ANTARES for new alerts associated with active GW events in TROVE"

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback-days-nle",
            help="Consider nonlocalized events active if event was discovered at MOST "+
            "this many days ago. POSITIVE number expected.",
            type=float,
            default=10,
        )
        parser.add_argument(
            "--test-num-alerts",
            help="For TESTING purposes only. Will limit the number of alerts processed to this number",
            type=int,
            default=sys.maxsize
        )
        parser.add_argument(
            "--test-num-gw",
            help="For TESTING purposes only. Will limit the number of GW events processed to this number",
            default=sys.maxsize,
            type=int
        )
        parser.add_argument(
            '--async-alert-processing',
            action=argparse.BooleanOptionalAction,
            default=True
        )
        
    def handle(self, lookback_days_nle=10, test_num_alerts=sys.maxsize, test_num_gw=sys.maxsize, async_alert_processing=True, **kwargs):

        active_gw_events = get_active_gw_events(lookback_days=lookback_days_nle)
        logger.info("Querying ANTARES for alerts associated with"+
                    f" {len(active_gw_events)} GW events")
        if test_num_gw:
            logger.info(f"In TESTING MODE, will only process the first {test_num_gw} GW events!")
        for idx, event_id in enumerate(active_gw_events):

            if idx >= test_num_gw:
                break
            
            loci = query_for_one_event(event_id)

            if test_num_alerts:
                logger.info(f"In TESTING MODE, will only process {test_num_alerts} alerts!")

            count = process_loci(
                loci,
                test_num_alerts=test_num_alerts,
                async_alert_processing=async_alert_processing,
                lookback_days_nle=lookback_days_nle,
                event_id=event_id
            )
            if async_alert_processing:
                logger.info(f"Async processing started for {count} loci associated with {event_id}")
            else:
                logger.info(f"Processed {count} loci associated with {event_id} in real time")
