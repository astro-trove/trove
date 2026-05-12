import time
import numpy as np

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection
from trove_targets.models import Target
from tom_targets.models import BaseTarget
from custom_code.alertstream_handlers import (
    pick_slack_channel,
    send_slack,
    vet_or_post_error,
)
from custom_code.templatetags.skymap_extras import get_preferred_localization
from datetime import datetime, timedelta, timezone
from custom_code.templatetags.target_extras import split_name
from candidate_vetting.models import TnsQ3C

# from slack_sdk import WebClient
import logging

from astropy.coordinates import SkyCoord
from healpix_alchemy.constants import HPX

logger = logging.getLogger(__name__)
new_format = logging.Formatter("[%(asctime)s] %(levelname)s : s%(message)s")
for handler in logger.handlers:
    handler.setFormatter(new_format)

# slack_tns = WebClient(settings.SLACK_TOKEN_TNS)
# slack_tns50 = WebClient(settings.SLACK_TOKEN_TNS50)
# slack_ep = WebClient(settings.SLACK_TOKEN_EP)


class Command(BaseCommand):
    help = "Updates, merges, and adds targets from the tns_q3c table (maintained outside the TOM Toolkit)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--lookback-days-nle",
            help="Nonlocalized events are considered active for this many days",
            type=float,
            default=7.0,
        )
        parser.add_argument(
            "--first-det-max",
            help="Associate transients whose first detection was within this "
            "many days of the nonlocalized event",
            type=float,
            default=10,
        )
        parser.add_argument(
            "--first-det-min",
            help="The minimum time since the NLE discovery for a first detection"
            " to be considered. A negative number (-1 is the default) will"
            " consider events with a first det. that many days before the NLE discovery.",
            type=float,
            default=-1,
        )

        parser.add_argument(
            "--vetting-start-time",
            help="The start time for vetting, we will vet everything from this time - "
            "vetting_lookback_horizon to this time",
            type=datetime,
            default=datetime.now(),
        )

        parser.add_argument(
            "--vetting-lookback-horizon",
            help="Any targets with a TNS discovery date of now - vetting_horizon days "
            "will be re-vet, including grabbing new photometry. The default is 2 weeks.",
            type=float,
            default=7,
        )

    def handle(
        self,
        lookback_days_nle=7.0,
        first_det_max=10,
        first_det_min=-1,
        vetting_start_time=datetime.now(),
        vetting_lookback_horizon=7,
        **kwargs,
    ):

        with connection.cursor() as cursor:
            cursor.execute("""
                 --STEP 0: update coordinates and prefix of existing targets with TNS names
                 UPDATE tom_targets_basetarget AS tt
                 SET name = CONCAT(tns.name_prefix, tns.name),
                     ra = tns.ra,
                     dec = tns.declination,
                     modified = NOW()
                 FROM tns_q3c AS tns
                 WHERE REGEXP_REPLACE(tt.name, '^[^0-9]*', '') = tns.name
                   AND (q3c_dist(tt.ra, tt.dec, tns.ra, tns.declination) > 0
                        OR tt.name != CONCAT(tns.name_prefix, tns.name))
                 RETURNING tt.id;
             """)
            updated_ids = [row[0] for row in cursor.fetchall()]
            updated_targets_coords = Target.objects.filter(id__in=updated_ids)

        logger.info(
            f"Updated coordinates of {len(updated_targets_coords):d} targets to match the TNS."
        )

        logger.info(
            "Crossmatching TNS with targets table. This will take several minutes."
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                --STEP 1: crossmatch TNS transients with existing targets and store in tns_matches table
                CREATE TEMPORARY TABLE tns_matches AS
                SELECT target.id, target.name, t.tns_name, t.sep, t.ra, t.dec
                FROM tom_targets_basetarget AS target LEFT JOIN LATERAL (
                    SELECT CONCAT(tns.name_prefix, tns.name) AS tns_name,
                        q3c_dist(target.ra, target.dec, tns.ra, tns.declination) AS sep,
                        tns.ra,
                        tns.declination as dec
                    FROM tns_q3c AS tns
                    WHERE q3c_join(target.ra, target.dec, tns.ra, tns.declination, 2. / 3600) AND name_prefix != 'FRB'
                    ORDER BY sep, discoverydate LIMIT 1 -- if there are duplicates in the TNS, use the earlier one
                ) AS t ON true
                WHERE t.tns_name IS NOT NULL;
                
                -- the top_tns_matches table tells you the target names and coordinates we are going to adopt
                CREATE TEMPORARY TABLE top_tns_matches AS
                SELECT DISTINCT ON (tns_name) *
                FROM tns_matches
                ORDER BY tns_name, name=tns_name desc, sep; -- prefer the one that already has the TNS name, if any
                
                -- after this, the tns_matches table tells you which targets need to be merged and deleted
                DELETE FROM tns_matches
                WHERE name IN (
                    SELECT name from top_tns_matches
                );
                """
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                --STEP 2: update existing non-TNS targets within 2" of a TNS transient to have the TNS name and coordinates
                UPDATE tom_targets_basetarget AS tt
                SET name=tm.tns_name, ra=tm.ra, dec=tm.dec, modified=NOW()
                FROM top_tns_matches AS tm
                WHERE tt.name=tm.name AND (tm.name != tm.tns_name OR sep > 0)
                RETURNING tt.id;
                """
            )
            updated_ids = [row[0] for row in cursor.fetchall()]
            updated_targets = Target.objects.filter(id__in=updated_ids)

        logger.info(f"Updated {len(updated_targets):d} targets to match the TNS.")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                --STEP 3: merge any other matches into the new target
                CREATE TEMPORARY TABLE targets_to_merge AS
                SELECT tm.id AS old_id, ttm.id  AS new_id, tm.name AS old_name, ttm.name AS new_name
                FROM tns_matches as tm
                JOIN top_tns_matches AS ttm ON ttm.name=tm.tns_name;
                
                UPDATE candidates
                SET targetid=new_id
                FROM targets_to_merge
                WHERE targetid=old_id;
                
                UPDATE tom_dataproducts_dataproduct
                SET target_id=new_id
                FROM targets_to_merge
                WHERE target_id=old_id;
                
                UPDATE tom_dataproducts_reduceddatum
                SET target_id=new_id
                FROM targets_to_merge
                WHERE target_id=old_id;
                
                UPDATE tom_nonlocalizedevents_eventcandidate
                SET target_id=new_id
                FROM targets_to_merge
                WHERE target_id=old_id;
                
                UPDATE tom_observations_observationrecord
                SET target_id=new_id
                FROM targets_to_merge
                WHERE target_id=old_id;
                
                UPDATE tom_targets_targetextra
                SET target_id=new_id
                FROM targets_to_merge
                WHERE target_id=old_id
                AND NOT EXISTS (
                    SELECT 1 FROM tom_targets_targetextra
                    WHERE target_id=new_id AND key=key
                );

                DELETE FROM tom_targets_targetextra
                WHERE target_id IN (SELECT old_id FROM targets_to_merge);
                
                UPDATE tom_targets_targetlist_targets
                SET basetarget_id=new_id
                FROM targets_to_merge
                WHERE basetarget_id=old_id;
                
                UPDATE tom_targets_targetname
                SET target_id=new_id
                FROM targets_to_merge
                WHERE target_id=old_id;
                """
            )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT old_id FROM targets_to_merge
                """
            )
            ids_to_delete = [row[0] for row in cursor.fetchall()]

        deleted_targets = Target.objects.filter(id__in=ids_to_delete)
        deleted_targets.delete()

        logger.info(f"Merged {len(deleted_targets):d} targets into TNS targets.")
        for target in deleted_targets:
            logger.info(f" - deleted target {target.name} during merge")

        with connection.cursor() as cursor:
            cursor.execute(
                """
                --STEP 4: add all other unmatched TNS transients to the targets table (removing duplicate names)
                INSERT INTO tom_targets_basetarget (name, type, created, modified, permissions, ra, dec, epoch, scheme)
                SELECT CONCAT(name_prefix, name), 'SIDEREAL', NOW(), NOW(), 'PUBLIC', ra, declination, 2000, ''
                FROM tns_q3c WHERE name_prefix != 'FRB' AND name != '2023hzc' -- this is a duplicate of AT2016jlf in the TNS
                ON CONFLICT (name) DO NOTHING
                RETURNING id;
                """
            )

            new_target_ids = [row[0] for row in cursor.fetchall()]
            new_targets = Target.objects.filter(id__in=new_target_ids)

        logger.info(f"Added {len(new_targets):d} new targets from the TNS.")

        # update the Trove Target table with redshift and classification info from TNS
        with connection.cursor() as cursor:
            cursor.execute(
                """
                -- Step 5: Update the trove_targets_target table with redshift and classifications from TNS
                UPDATE trove_targets_target AS tt
                  SET redshift = tns.redshift,
                      classification = tns.objtype
                  FROM tns_q3c AS tns
                  INNER JOIN tom_targets_basetarget AS bt
                  ON tns.name = REGEXP_REPLACE(bt.name, '^[^0-9]*', '')
                  WHERE bt.id = tt.basetarget_ptr_id AND (
                      tt.redshift IS NULL OR
                      tt.redshift = 'nan'
                  )
                RETURNING tt.basetarget_ptr_id;
                """
            )

            updated_target_ids = [row[0] for row in cursor.fetchall()]
        logger.info(
            f"Updated {len(updated_target_ids):d} targets with classifications and/or redshifts from the TNS."
        )

        # Finally, we need to insert these into the Trove Target table rather than
        # just the TOM BaseTarget table

        # these missing_targets should be the ones that are added to the BaseTarget
        # table but not the Trove Targets table
        # note that we will recompute the healpix, etc. below. These are just
        # temporary placeholders
        missing_targets = BaseTarget.objects.filter(target__isnull=True)
        logger.info(
            f"Adding {len(missing_targets):d} from basetarget table to trove target table"
        )
        for basetarget in missing_targets:
            # create the target with the basetarget ptr
            coord = SkyCoord(basetarget.ra, basetarget.dec, unit="deg")
            with connection.cursor() as cursor:
                cursor.execute(f"""
                INSERT INTO trove_targets_target (
                    basetarget_ptr_id,
                    healpix
                )
                VALUES (
                    {basetarget.id},
                    {HPX.skycoord_to_healpix(coord)}
                )
                """)

        # now we need to vet all targets from the past vetting_horizon days
        recent_tns_transients = TnsQ3C.objects.filter(
            discoverydate__gte=datetime.now()
            - timedelta(days=vetting_lookback_horizon),
            discoverydate__lte=vetting_start_time,
        )
        tns_names = [q.name_prefix + q.name for q in list(recent_tns_transients)]
        targets_to_vet = Target.objects.filter(name__in=tns_names)
        logger.info(
            f"Vetting {len(tns_names)} targets from TNS, this might take a bit..."
        )
        vetting_start = time.time()
        for ii, trove_target in enumerate(targets_to_vet):
            single_vetting_start = time.time()
            vet_or_post_error(
                trove_target,
                # slack_tns,
                # channel='alerts-tns',
                lookback_days_nle=lookback_days_nle,
                first_det_max=first_det_max,
                first_det_min=first_det_min,
                # then only vet if there is new photometry and no updates to this target
                skip_vet_if_no_new_phot=(
                    trove_target.basetarget_ptr_id
                    not in updated_target_ids + new_target_ids
                ),
                use_async_mpc=True,
            )
            single_vetting_time = time.time() - single_vetting_start
            logger.info("##########################################################")
            logger.info(
                f"[{ii + 1}/{len(tns_names)}] Vetting {trove_target.name} took {single_vetting_time:.2f}s. "
                + f"So far, vetting of TNS transients has taken {(time.time() - vetting_start) / 60:.2f}m"
            )
            logger.info("##########################################################")

        total_vetting_time = time.time() - vetting_start
        logger.info(
            f"Vetting took {total_vetting_time / 60:.2f}m with an average of "
            + f"{total_vetting_time / len(tns_names):.2f}s per target"
        )
