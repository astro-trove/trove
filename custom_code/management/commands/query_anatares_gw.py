"""
This management command queries the ANTARES broker for loci associated with
"active" GW events in their database
"""
from django.core.management.base import BaseCommand
from antares_client.search import search
from elasticsearch_dsl import Search as ES_Search, Q as ES_Q

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

    ztf_not_sso = ES_Q("term", **{"properties.ztf_ssnamenr": "null"})
    lsst_not_sso = ES_Q("term", **{"properties.lsst_diaSource_ssObjectId": 0})
    neither_key = ES_Q(
        "bool",
        must_not=[
            Q("exists", field="properties.ztf_ssnamenr"),
            Q("exists", field="properties.lsst_diaSource_ssObjectId"),
        ],
    )

    query = (
        ES_Search()
        .filter("term", **{"grav_wave_events.keyword": event_id})
        .filter(ztf_not_sso | lsst_not_sso | neither_key)
        .to_dict()
    )
    loci = search(query)

    import pdb; pdb.set_trace()
    
    return
    
class Command(BaseCommand):
    help = ""

    def handle(self, **kwargs):
        
