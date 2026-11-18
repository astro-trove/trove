Submitting Galaxy Spec-z's
==========================

During a follow-up campaign, if a potential host galaxy for some candidate has only a 
photometric redshift, you might measure the host's spectroscopic redshift. 
This is incredibly useful to the community, so we encourage you to submit that 
redshift to TROVE so it may be used in vetting/scoring by the rest of the 
community.

Note that while TNS accepts redshifts for transients, it does not for galaxies! There 
is no central repository for redshifts measured during the course of a follow-up 
campaign, and combing through GCNs can be a pain. We try to make this process more 
streamlined.

We'll walk through an example here. Imagine we're curious about target 
AT2025adks and its association with GW event S251112cm. Here is the page for 
the target:

.. image:: hostredshiftimg/0_AT2025adks_before.png

|

When the candidate was first vetted, it was associated with galaxy 
J011512.84-474115.7, with a photometric redshift of 0.07 ± 0.04. Because 
S251112cm lies at 93 Mpc (z ~ 0.02), the distance score for AT2025adks is 
quite low. 

You notice that the galaxy also has a redshift in LS DR10. You find this galaxy 
promising, so you add a comment letting the community know you're pursuing it.

.. image:: hostredshiftimg/1_AT2025adks_commented.png

|

Later, you obtain a spectroscopic redshift of 0.023 ± 0.002 ! Clicking "Update Host z" 
on the candidate's page, you submit the following measurement: 

.. image:: hostredshiftimg/2_AT2025adks_updatez.png

|

Upon clicking Update Selected Galaxy Redshift, the selected galaxy will 
essentially be duplicated in our database, *but*, with the redshift you provided! 
That galaxy will be assigned a "user spec-z" for its distance type, which has highest 
priority for use in distance scoring after a direct measurement of the distance to the 
candidate. Finally, the candidate will be re-vet. When the page finishes reloading:

.. image:: hostredshiftimg/3_AT2025adks_after.png

|

The galaxy whose redshift you measured is now included in the galaxy table and is 
designated the most likely host. The distance score is much higher because you found 
that the galaxy is at exactly the right distance, and now the rest of the community 
knows that this is an excellent candidate worth following up on.