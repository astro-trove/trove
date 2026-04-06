"""
This will check for ZTF forced photometry in the TROVE email. I'm making this a
command so that we can run it in a cronjob
"""

from django.core.management.base import BaseCommand
from tom_nonlocalizedevents.models import NonLocalizedEvent
from django.conf import settings
from datetime import datetime
import requests
import logging

from candidate_vetting.public_catalogs.phot_catalogs import ZTF_Forced_Phot

logger = logging.getLogger(__name__)

class Command(BaseCommand):

    help = "Checks for new ZTF forced photometry in the TROVE gmail"

    def handle(self, **kwargs):
        ztf = ZTF_Forced_Phot()
        logger.info("Checking for new data")
        res = []
        try:
            res = ztf.check_for_new_data()
        except Exception as exc:
            # I'm wrapping this in a try-except just in case
            # we don't want it crashing everything!
            logger.exception(exc)

        if len(res):
            logger.info(f"Found data and uploaded to {', '.join(res)}")
        else:
            logger.info(f"Found no new forced photometry to upload!")
