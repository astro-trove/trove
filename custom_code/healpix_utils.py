from django.conf import settings
from django.db import transaction
from django.db.utils import IntegrityError
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, Session
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, SkymapTile
from tom_nonlocalizedevents.healpix_utils import sa_engine, SaSkymapTile, uniq_to_bigintrange
from tom_nonlocalizedevents.healpix_utils import update_all_credible_region_percents_for_candidates
from tom_targets.models import Target
import numpy as np
from scipy.stats import multivariate_normal
from ligo.skymap.moc import bayestar_adaptive_grid
from datetime import datetime, timezone
import hashlib
import uuid
import sys
import json
import logging

logger = logging.getLogger(__name__)

CREDIBLE_REGION_PROBABILITIES = sorted(json.loads(settings.CREDIBLE_REGION_PROBABILITIES), reverse=True)

Base = declarative_base()


class SaTargetExtra(Base):
    __tablename__ = 'tom_targets_targetextra'
    id = sa.Column(sa.Integer, primary_key=True)
    target_id = sa.Column(sa.Integer, nullable=False)
    key = sa.Column(sa.String, nullable=False)
    value = sa.Column(sa.String, nullable=False)


def create_candidates_from_targets(eventsequence, prob=0.95, target_ids=None):
    """
    Creates an EventCandidate for each target that falls within the `prob` credible region of the localization region
    associated with `eventsequence`. If no `target_ids` are given, all targets created after the
    event time are considered.
    """
    if target_ids is None:
        targets = Target.objects.filter(created__gte=eventsequence.details['time'])
        target_ids = list(targets.values_list('pk', flat=True))

    with Session(sa_engine) as session:

        cum_prob = sa.func.sum(
            SaSkymapTile.probdensity * SaSkymapTile.tile.area
        ).over(
            order_by=SaSkymapTile.probdensity.desc()
        ).label(
            'cum_prob'
        )

        subquery = sa.select(
            SaSkymapTile.probdensity,
            cum_prob
        ).filter(
            SaSkymapTile.localization_id == eventsequence.localization.id
        ).subquery()

        min_probdensity = sa.select(
            sa.func.min(subquery.columns.probdensity)
        ).filter(
            subquery.columns.cum_prob <= prob
        ).scalar_subquery()

        query = sa.select(
            SaTargetExtra.target_id
        ).filter(
            SaTargetExtra.target_id.in_(target_ids),
            SaTargetExtra.key == 'healpix',
            SaSkymapTile.localization_id == eventsequence.localization.id,
            SaSkymapTile.tile.contains(sa.cast(SaTargetExtra.value, sa.BigInteger)),
            SaSkymapTile.probdensity >= min_probdensity
        )

        results = session.execute(query)

        new_candidates = []
        for result in results:
            ec, created = EventCandidate.objects.get_or_create(
                target=Target.objects.get(id=result[0]),
                nonlocalizedevent=eventsequence.nonlocalizedevent,
            )
            if created:
                new_candidates.append(ec)

        logger.info(f'Linked {len(new_candidates)} new candidates to event {eventsequence.nonlocalizedevent.event_id}')

        localizations = eventsequence.nonlocalizedevent.localizations.all()
        for localization in localizations:
            update_all_credible_region_percents_for_candidates(localization, [cand.id for cand in new_candidates])

        return new_candidates


def create_elliptical_localization(nonlocalizedevent, center, radius, conf_inv=0.9):
    """
    Create an elliptical healpix localization with a Gaussian probability distribution

    :param nonlocalizedevent: connect the localization to this nonlocalized event
    :param center: center of the ellipse [ra, dec] in degrees
    :param radius: radius of the ellipse [ra, dec] in degrees (a single float can be given for a circular localization)
    :param conf_inv: confidence interval corresponding to the given radius (default: 0.9 = 90%)
    """
    logger.info(f"Creating localization for {nonlocalizedevent.event_id} at {center} with radius {radius}")

    center = np.deg2rad(center)
    if isinstance(radius, float) or isinstance(radius, int):
        radius = np.tile(radius, 2)
    sigma = np.deg2rad(radius) / np.sqrt(-2. * np.log(1. - conf_inv))  # converting from conf_inv to 2D Gaussian sigma
    distribution = multivariate_normal(center, np.diag(sigma))
    skymap = bayestar_adaptive_grid(distribution.pdf)

    # rather than make a fake skymap file, encode the unique parameters in a short string
    skymap_bytes = '{:f}_{:f}_{:f}_{:f}_{:f}_{:f}'.format(*distribution.mean, *distribution.cov.flat).encode('utf-8')
    skymap_hash = hashlib.md5(skymap_bytes).hexdigest()
    skymap_uuid = uuid.UUID(skymap_hash)
    try:
        localization = EventLocalization.objects.get(nonlocalizedevent=nonlocalizedevent, skymap_hash=skymap_uuid)
    except EventLocalization.DoesNotExist:
        date = datetime.now(tz=timezone.utc)

        # calculate localization areas analytically, assuming localization is small (so we can pretend it's Euclidean)
        radius_50 = np.rad2deg(distribution.cov) * np.sqrt(-2. * np.log(0.5))
        radius_90 = np.rad2deg(distribution.cov) * np.sqrt(-2. * np.log(0.1))
        area_50 = np.pi * np.linalg.det(radius_50)
        area_90 = np.pi * np.linalg.det(radius_90)

        with transaction.atomic():
            try:
                localization, is_new = EventLocalization.objects.get_or_create(
                    nonlocalizedevent=nonlocalizedevent,
                    skymap_hash=skymap_uuid,
                    defaults={
                        'area_50': area_50,
                        'area_90': area_90,
                        'date': date
                    }
                )
                if not is_new:
                    # This is added to protect against race conditions where the localization has already been added
                    return localization
                for i, row in enumerate(skymap):
                    # This is necessary to make sure we don't get an underflow error in postgres
                    # when operating with the probdensity float field
                    probdensity = row['PROBDENSITY'] if row['PROBDENSITY'] > sys.float_info.min else 0
                    SkymapTile.objects.create(
                        localization=localization,
                        tile=uniq_to_bigintrange(row['UNIQ']),
                        probdensity=probdensity,
                    )
            except IntegrityError as e:
                if 'unique constraint' in e.message:
                    return EventLocalization.objects.get(nonlocalizedevent=nonlocalizedevent, skymap_hash=skymap_hash)
                raise e

    return localization, skymap
