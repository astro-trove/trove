"""
This management command queries the ANTARES broker for loci associated with
"active" GW events in their database
"""
from django.core.management.base import BaseCommand
from antares_client.search import search

def process_single_locus(locus):
    """
    This processes a single locus and ingest the data/photometry/target info into the
    trove database
    """

def process_loci(loci):
    """
    This processes a list of loci and ingests the data and/or target info into the
    TROVE database
    """
    for locus in loci:
        process_single_locus(locus)

def get_active_gw_events():
    """
    This gets a list of active GW events stored in the TROVE database
    """
    return []

def query_for_one_event(event_id:str):
    """
    Given an event_id of a GraceDB GW event this queries ANTARES for all "loci"
    (essentially their terminology for alert packets) associated with that GW event
    """
    query = {
        "query": {
            "bool": {
                "filter": {
                    "term": {
                        "grav_wave_events.keyword": event_id
                    },
                },
            },
        }
    }

    loci = search(query)

    # TODO: do we want to process the loci here? Or somewhere else?
    return loci

class Command(BaseCommand):
    help = ""

    def handle(self, **kwargs):
        
