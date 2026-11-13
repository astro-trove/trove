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
AT2025alpk and its association with GW event S251112cm. Here is the page for 
the target:

.. image:: hostredshiftimg/0_AT2025alpk_before.png

|

When the candidate was first vetted, it was associated with galaxy 
J233413.14-240417.7, with a photometric redshift of 0.07 ± 0.04. Because 
S251112cm lies at 93 Mpc (z ~ 0.02), the distance score for AT2025alpk is 
quite low. 

You notice that the galaxy also has a redshift in PS1 STRM. You find this galaxy 
promising, so you add a comment letting the community know you're pursuing it.

.. image:: hostredshiftimg/1_AT2025alpk_commented.png

|

Later, you obtain a spectroscopic redshift of 0.021 ± 0.001 ! Clicking "Update Host z" 
on the candidate's page, you submit the following measurement: 

.. image:: hostredshiftimg/2_AT2025alpk_updatez.png

|

Upon clicking Update Selected Galaxy Redshift, the selected galaxy will 
essentially be duplicated in our database, *but*, with the redshift you provided! 
That galaxy will be assigned a "user spec-z" for its distance type, which has highest 
priority for use in distance scoring after a direct measurement of the distance to the 
candidate. Finally, the candidate will be re-vet. When the page finishes reloading:

.. image:: hostredshiftimg/3_AT2025alpk_after.png

|

The galaxy whose redshift you measured is now designated the most likely host, 
and the distance score is much higher because you found that the galaxy is at exactly 
the right distance!