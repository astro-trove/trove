from django.conf import settings
from healpix_alchemy.types import Point
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, Session
from tom_nonlocalizedevents.models import EventCandidate
from tom_nonlocalizedevents.healpix_utils import sa_engine, SaSkymapTile
from tom_surveys.models import SurveyField
from tom_targets.models import Target
from .models import SurveyFieldCredibleRegion
import json
import logging

logger = logging.getLogger(__name__)

CREDIBLE_REGION_PROBABILITIES = sorted(json.loads(settings.CREDIBLE_REGION_PROBABILITIES), reverse=True)

Base = declarative_base()


class SaSurveyField(Base):
    __tablename__ = 'tom_surveys_surveyfield'
    name = sa.Column(sa.String, primary_key=True)
    healpix = sa.Column(Point)


def update_all_credible_region_percents_for_survey_fields(eventlocalization):
    """
    This function creates a credible region linkage for each of the survey fields in the event localization specified
    """
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
            SaSkymapTile.localization_id == eventlocalization.id
        ).subquery()

        for prob in CREDIBLE_REGION_PROBABILITIES:
            min_probdensity = sa.select(
                sa.func.min(subquery.columns.probdensity)
            ).filter(
                subquery.columns.cum_prob <= prob
            ).scalar_subquery()

            query = sa.select(
                SaSurveyField.name
            ).filter(
                SaSkymapTile.localization_id == eventlocalization.id,
                SaSkymapTile.tile.contains(SaSurveyField.healpix),
                SaSkymapTile.probdensity >= min_probdensity
            )

            results = session.execute(query)

            for sa_survey_field in results:
                SurveyFieldCredibleRegion.objects.update_or_create(
                    survey_field = SurveyField.objects.get(name=sa_survey_field[0]),
                    localization=eventlocalization,
                    defaults={
                        'smallest_percent': int(prob * 100.0)
                    }
                )
    logger.info('Updated credible regions for survey fields')


class SaTargetExtra(Base):
    __tablename__ = 'tom_targets_targetextra'
    id = sa.Column(sa.Integer, primary_key=True)
    target_id = sa.Column(sa.Integer, nullable=False)
    key = sa.Column(sa.String, nullable=False)
    value = sa.Column(sa.String, nullable=False)


def create_candidates_from_targets(eventsequence, prob=0.95, target_ids=None):
    """
    Creates an EventCandidate for each target that falls within the `prob` credible region of the localization region
    associated with `eventsequence`. If no `target_ids` are given, all targets (not starting with "J") created after the
    event time are considered.
    """
    if target_ids is None:
        targets = Target.objects.exclude(name__startswith='J').filter(created__gte=eventsequence.details['time'])
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
        return new_candidates
