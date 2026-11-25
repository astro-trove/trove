How Scoring Works
=================

This page very briefly describes the various subscores which go into the total, overall 
score. This overall score is a number between 0 and 1, and is a product of several other 
numbers (subsocres) which themselves are between 0 and 1. For details, see Franz et 
al., in prep. 


Localization (2D) Subscores
---------------------------

The 2D, skymap, or localization subscore quantifies whether a candidate sits squarely in the 
localization region of some event or on the edge. We compute this score by finding the 
HEALPix in which a candidate lies, extracting the probability density from the skymap 
at that pixel, and then integrating over pixels at a higher probability density. 
Integration over all pixels is normalized to 1, such that the 2D subscore lies 
between 0 and 1.

The following cartoon explains the 2D score graphically:

.. image:: explainedimg/2D_scoring.png

|

Darker colors denote higher probability density.


Distance (3D) Subscores
-----------------------

The distance subscore quantifies whether the estimated distance to a candidate is 
consistent with the distance of some event such as a gravitational wave. Distances to 
candidates are primarily estimated by associating them with a potential host galaxy 
at some measured distance. We perform this galaxy association using about 10 billion 
galaxies over several catalogs. For a table of which galaxy catalogs are used, see 
`Catalogs Used in TROVE <catalogs.html>`_. Distances may also come from spectroscopy of 
individual candidates themselves. 

The computation of the distance subscore has two regimes:

1. Uncertainty on galaxy distance :math:`\ll` uncertainty on event distance (:math:`\sigma_{\rm gal} \ll \sigma_{\rm event}`): 
   The distance subscore roughly depends on how many standard deviations :math:`\sigma_{\rm event}` away from the median the 
   distance to the galaxy lies. This is the case for most spectroscopic redshifts given
   their tight uncertainties.
2. Uncertainty on galaxy distance > uncertainty on event distance (:math:`\sigma_{\rm gal} > \sigma_{\rm event}`): 
   We use the Bhattacharyya coefficient, which computes something like the overlap of 
   the two distance posterior distributions. This is the case for most photometric redshifts, 
   as they have broader uncertainties. 


The following figure from Franz et al., in prep explains this distance score 
graphically, where the event in question is a gravitational wave: 

.. image:: explainedimg/distance_scoring.png

|

We see a comparison of the galaxy / event distance posteriors and the resultant scores. 
The four scenarios shown are seen across candidates during a follow-up campaign. 


Point Source and Solar System Object Subcores
---------------------------------------------

Candidates are disfavored (receive a subscore of 0) or favored (receive a subscore of 1) 
based on their association with point sources. These point sources are typically stars 
or variable stars; we perform cross-matching against the AGN included in some of these 
point source catalogs separately. For a table of which point source catalogs are used, 
see `Catalogs Used in TROVE <catalogs.html>`_.

Candidates are disfavored (receive a subscore of 0) or favored (receive a subscore of 1) 
based on their association with solar system objects (SSOs). We obtain a list of 
SSOs and their ephemerides from the Minor Planet Center (MPC). We only perform SSO 
association if a candidate is limited to a single photometric detection, as multiple 
detections at the same RA and Dec. (within uncertainties of ~2" for the 
astrometric precision of a candidates' coordinates) would be inconsistent with an SSO. 
If any SSOs are within 10° of a candidate at time of its initial (and sole) detection, 
we compute the full :math:`N`-body dynamics of the SSOs. We consider a candidate 
associated with an SSO if it is within 25" of an SSO at the time of its initial (and 
sole) detection. 

AGN Subscores
-------------


Photometry Scores
-----------------

