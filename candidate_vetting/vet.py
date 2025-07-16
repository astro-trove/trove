"""
Vetting code for non-localized events with transients
"""

from custom_code.models import Target, SurveyFieldCredibleRegion
from tom_nonlocalizedevents.models import EventCandidate, EventLocalization, SkymapTile
from tom_nonlocalizedevents.healpix_utils import (
    sa_engine,
    SaSkymapTile,
    uniq_to_bigintrange,
    update_all_credible_region_percents_for_candidates
)

def 2d_association(localization:EventLocalization, target:Target, prob:float=0.95):

    with Session(sa_engine) as session:

        # calculate the cumalative probability density for the tiles
        # SHOULDN'T THIS BE STORED IN THE SaSkymapTile OBJECT???
        cum_prob = sa.func.sum(
            SaSkymapTile.probdensity * SaSkymapTile.tile.area
        ).over(
            order_by=SaSkymapTile.probdensity.desc()
        ).label(
            'cum_prob'
        )

        # find the localization region in the SaSkymapTile
        subquery = sa.select(
            SaSkymapTile.probdensity,
            cum_prob
        ).filter(
            SaSkymapTile.localization_id == localization.id
        ).subquery()

        # Filter on the skymap and take all of the tiles that are within the
        # cumulative probability density contour passed in as "prob"
        min_probdensity = sa.select(
            sa.func.min(subquery.columns.probdensity)
        ).filter(
            subquery.columns.cum_prob <= prob
        ).scalar_subquery()

        # write the query for the Target table
        query = sa.select(
            Target.id
        ).filter(
            Target.id.in_(target_ids),
            SaSkymapTile.localization_id == localization.id,
            SaSkymapTile.tile.contains(sa.cast(Target.healpix, sa.BigInteger)),
            SaSkymapTile.probdensity >= min_probdensity
        )

        # execute the query
        results = session.query(query)
    
        
