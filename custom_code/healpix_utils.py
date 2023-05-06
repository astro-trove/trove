from django.conf import settings
from healpix_alchemy.types import Point
import sqlalchemy as sa
from sqlalchemy.orm import declarative_base, Session
from tom_nonlocalizedevents.healpix_utils import sa_engine, SaSkymapTile
from .models import CSSField, CSSFieldCredibleRegion
import json


CREDIBLE_REGION_PROBABILITIES = sorted(json.loads(settings.CREDIBLE_REGION_PROBABILITIES), reverse=True)

Base = declarative_base()


class SaCSSField(Base):
    __tablename__ = 'custom_code_cssfield'
    name = sa.Column(sa.String, primary_key=True)
    healpix = sa.Column(Point)


def update_all_credible_region_percents_for_css_fields(eventlocalization):
    """
    This function creates a credible region linkage with probability prob for each of the event candidate
    ids supplied if they fall within that prob for the event location specified.
    """
    session = Session(sa_engine)

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
            SaCSSField.name
        ).filter(
            SaSkymapTile.localization_id == eventlocalization.id,
            SaSkymapTile.tile.contains(SaCSSField.healpix),
            SaSkymapTile.probdensity >= min_probdensity
        )

        results = session.execute(query)

        for sa_css_field in results:
            CSSFieldCredibleRegion.objects.update_or_create(
                css_field=CSSField.objects.get(name=sa_css_field[0]),
                localization=eventlocalization,
                defaults={
                    'smallest_percent': int(prob * 100.0)
                }
            )