Submitting Galaxy Spec-z's
==========================

During a follow-up campaign, if a potential host galaxy for some candidate has only a 
photometric redshift, you might measure the host's spectroscopic redshift. 
This is incredibly useful to the community, so we encourage you to submit that 
redshift to TROVE so it may be used in vetting/scoring by the rest of the 
community.

Note that while TNS accepts redshifts for targets, it does not for galaxies!

We'll walk through an example here. Imagine we're curious about target 
AT2025adhg and its association with GW event S251112cm. Here is the page for 
the target:

.. image:: hostredshiftimg/adhg-before.png

|

When the candidate was vetted, it was associated with galaxy 
WISEA J002827.13-361543.4, with a spectroscopic redshift of 0.044 ± 0.001. Because 
S251112cm lies at 93 Mpc (z ~ 0.02), the distance association score for AT2025adhg is 
quite low. 

Perhaps you believed that the LS DR10 galaxy with a photometric redshift was 
the real host, and acquired a spectrum of it, finding it lies at a spectroscopic 
redshift of 0.021 ± 0.001. Clicking on the Update Host z button, you are taken to a form. 
You fill it out as such:

.. image:: hostredshiftimg/redshift-form.png

|

Upon clicking Update Selected Galaxy Redshift, that LS DR10 galaxy will 
essentially be duplicated in our database, *but*, with the redshift you provided! 
That galaxy will be assigned a "user spec-z" for its distance type, which has highest 
priority after a direct measurement of the distance to the candidate. Finally, 
the candidate will be re-vet. When the page finishes reloading:

.. image:: hostredshiftimg/adhg-after.png

The galaxy whose redshift you measured is now designated the most likely host, 
and the distance score is increased! 