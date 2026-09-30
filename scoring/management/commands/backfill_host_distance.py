"""
Fill in ``host_distance`` for candidates vetted before it was recorded.

Vetting works the distance out to score it, and now stores it alongside
``host_distance_score``. Candidates vetted earlier have the score but not the
distance, so the UI would show a gap for them:

    python manage.py backfill_host_distance

This recomputes the distance from each candidate's saved host galaxy table --
it is not a re-vet, and no other score factor is touched.
"""

import io
import logging

import numpy as np
import pandas as pd
from astropy import units as u
from django.conf import settings
from django.core.management.base import BaseCommand
from tom_targets.models import TargetExtra

from scoring.models import ScoreFactor
from scoring.scoring import (
    DistanceScore,
    _host_used_for_distance_scoring,
    clean_host_df,
    store_host_distance,
)
from trove_targets.models import Target

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Store host_distance for candidates that were vetted before it was "
        "recorded. Recomputes from saved host galaxies; does not re-vet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int,
            help="Stop after this many candidates. Default is all of them.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be written without writing it.",
        )

    def handle(self, *args, **options):
        # a distance score means a distance was used, so one can be recovered
        scored = set(ScoreFactor.objects.filter(key="host_distance_score")
                     .values_list("event_candidate_id", flat=True))
        done = set(ScoreFactor.objects.filter(key="host_distance")
                   .values_list("event_candidate_id", flat=True))
        todo = sorted(scored - done)
        if options["limit"]:
            todo = todo[: options["limit"]]

        if not todo:
            self.stdout.write("Every vetted candidate already has a distance.")
            return

        self.stdout.write(f"Backfilling {len(todo)} candidate(s)...")
        if options["dry_run"]:
            return

        written = skipped = failed = 0
        candidates = ScoreFactor.objects.filter(
            key="host_distance_score", event_candidate_id__in=todo
        ).select_related("event_candidate__nonlocalizedevent")
        for row in candidates:
            ec = row.event_candidate
            try:
                found = self.distance_for(ec)
            except Exception:
                logger.exception("distance backfill failed for candidate %s", ec.id)
                failed += 1
                continue
            if found is None:
                skipped += 1
                continue
            dist, neg, pos = found
            store_host_distance(ec, DistanceScore(None, None, None, dist, neg, pos))
            written += 1

        self.stdout.write(self.style.SUCCESS(
            f"Wrote {written} distance(s); skipped {skipped} with none to recover"
            + (f"; {failed} failed" if failed else "")
        ))

    def distance_for(self, event_candidate):
        """The distance this candidate was scored against, or None.

        Only the two sources vetting itself uses: the target's own redshift, or
        the exact host galaxy recorded in host_name/host_catalog. Deliberately
        no fall back to the event's distance at that sky position, which is not
        this candidate's distance and would be wrong under this key.
        """
        target = Target.objects.get(id=event_candidate.target_id)
        if target.redshift is not None and not np.isnan(target.redshift):
            cosmo = settings.COSMO
            dist = cosmo.luminosity_distance(target.redshift).to(u.Mpc).value
            err = cosmo.luminosity_distance(1e-3).to(u.Mpc).value
            return dist, err, err

        hosts = TargetExtra.objects.filter(
            target_id=event_candidate.target_id, key="Host Galaxies"
        ).first()
        if hosts is None or not hosts.value:
            return None
        host_df = clean_host_df(pd.read_json(io.StringIO(hosts.value)))
        if not len(host_df):
            return None
        host = _host_used_for_distance_scoring(
            host_df, event_candidate.target_id,
            event_candidate.nonlocalizedevent.event_id,
        )
        if host is None:
            return None
        err = getattr(host, "DistErr", None)
        neg, pos = (err if isinstance(err, (list, tuple)) and len(err) == 2
                    else (err, err))
        return host.Dist, neg, pos
