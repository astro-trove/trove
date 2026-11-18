FAQ & Glossary
==============

Frequently Asked Questions (FAQ)
--------------------------------

.. dropdown:: Why TROVE?

    To optimize follow-up of candidate counterparts to poorly localized events. We 
    can't control event rates or (without lots of money) build enough telescopes and 
    spectrographs to obtain conclusive datasets for every candidate counterpart. We 
    *can* choose how to best use our limited resources!

.. dropdown:: What does TROVE do which other tools do not?

    The `Transient Name Server (TNS) <https://www.wis-tns.org/>`_ acts a repository for 
    photometry and spectroscopy. `GCNs <https://gcn.nasa.gov/>`_ also contain some of 
    this data (though sometimes in a less machine-readable way) and information on 
    executed observations. `Brokers <https://rubinobservatory.org/for-scientists/data-products/alerts-and-brokers>`_ 
    curate photometry from large-sky surveys. The `GW Treasure Map <https://treasuremap.space/>`_ 
    is a platform for users to share telescope pointings (planned or executed) to inform 
    the community which parts of a given localization volume are already being covered. 
    
    TROVE assigns scores to candidate counterparts to some poorly localized event based 
    on their consistency with the expected counterpart (e.g., kilonova counterparts to
    binary neutron star mergers) and produces a ranked list of scores. This allows for 
    prioritizing targeted observations of candidates of interest, and is designed to 
    encourage community collaboration around pursuing not just the sole most promising 
    candidate but, e.g., the top decile of promising candidates as ranked by TROVE.

.. dropdown:: Why public?

    Open communication in the community about planned and executed observations and public
    sharing of photometry, spectroscopy, and redshift measurements are most likely to 
    minimize redundant observations and find us the next kilonova. 

.. dropdown:: If it's public, why do I need an account?

    Bots have the nasty habit of crawling through websites and clicking every button 
    that can possibly be clicked. To not immediately overwhelm our servers, you must 
    make an account to click those buttons. You must also agree to some basic rules 
    about conduct when making that account. 

.. dropdown:: Is there some sort of TROVE community space?

    Yes! Join the `Slack workspace <https://join.slack.com/t/astrotrove/shared_invite/zt-4fgc3msme-31VxfdBs_HYYcMpBJ0_UrQ>`_.
    
    You may also comment on individual candidates to let the community know about 
    planned or executed observations. See also questions below on sharing photometry 
    and spectroscopy.

.. dropdown:: How should I interpret a score?

    A high score for a candidate means that a candidate is not inconsistent with the 
    expected counterpart based on currently available data, and merits follow-up. 
    
    Importantly, scores for candidates should *not* be compared across different poorly 
    localized events (PLEs). Rather, the *ranking* of candidates' scores for a given GW 
    event should be used to drive follow-up. For example: A candidate with a score of 
    0.10 could be ranked far down the list (e.g., if data is sparse for other 
    candidates and other candidates remain consistent with the expected counterpart), 
    or, in the top decile (e.g., if all candidates lie on the edge of a localization 
    region due to observing constraints).
    
.. dropdown:: How do I compare scores? 

    See "How should I interpret a score" above. Scores for *candidates* should not be 
    compared across different poorly localized events (PLEs).
        
.. dropdown:: Should I go after the highest-ranked candidate on TROVE?

    Not necessarily! Except in exceptional cases (e.g., AT2017gfo and GW170817), as a 
    *community*, we are more likely to find the next kilonova if we collectively curate 
    a dataset for the top decile of candidates such that we rule out interlopers and 
    build conclusive datasets for promising candidates.

.. dropdown:: I want a list of candidates to plan observations. How do I do that?

    Use the `API <api.html>`_ or go the page for an event, select candidates of 
    interest, and export them to a CSV.

.. dropdown:: I'm obtaining photometry or a spectrum for a candidate on TROVE. Can I inform the community?

    Yes! Leave a comment on the candidate's page letting the community know what 
    observations you're planning, with what instrument, and what depths you expect to 
    reach. This will reduce waste and redundant observations, and a member of the 
    community might tweak their own observing strategy based on what you share.


.. dropdown:: I've obtained photometry for a candidate in TROVE. What is the best way to share it?

    Upload that photometry to the TNS and re-vet the candidate on its page. 

.. dropdown:: I've obtained a spectrum (and maybe a redshift) for a candidate in TROVE. What is the best way to share it?

    Upload that spectrum (and report the measured redshift, if relevant) to the TNS and 
    re-vet the candidate on its page. 
    
.. dropdown:: I've obtained a spectrum for the *potential host galaxy* of a candidate in TROVE. What is the best way to share it?

    See `Submitting Galaxy Spec-z's & Glossary <updatingredshifts.html>`_ !
    
.. dropdown:: Which parts of TROVE are automated? Which aren't?

    Automated: Ingestion of poorly localized events (PLEs), transients from TNS, LSST, 
    and ZTF, updates to transient metadata from TNS, association of transients with 
    PLEs, real-time vetting of new candidates, ingestion of new photometry from LSST and 
    ZTF, and ATLAS forced photometry (as long as it will be constraining for the light 
    curve of the transient).
    
    Unautomated at present, due to technical limitations: ZTF forced photometry, 
    updates to TNS light curves, re-vetting of *all* candidates associated with a PLE, 
    ingestion of photometry from all-sky surveys without some Kafka stream, and 
    ingestion of galaxies' spectroscopic redshifts obtained by various teams during a 
    follow-up campaign (see `Submitting Galaxy Spec-z's & Glossary <updatingredshifts.html>`_).


Glossary of Frequently Used Terms
---------------------------------

.. dropdown:: Glossary
    :open:

    **AGN**: Active galactic nucleus / active galactic nuclei
    
    **ATLAS**: The Asteroid Terrestrial-impact Last Alert System. A network of 
    telescopes around the world. We use ATLAS' forced photometry server to augment the 
    lightcurves of *targets*.
    
    **API**: Application programming interface
    
    **BBH**: Binary black hole merger
    
    **BNS**: Binary neutron star merger
    
    **candidate**: Sometimes used as shorthand for event candidate.
    
    **event**: Sometimes used as shorthand for *poorly localized / nonlocalized event*.

    **event candidate**: A *target* which has been associated with a *poorly localized 
    event (PLE)*. A candidate electromagnetic counterpart to said PLE.
    
    **forced photometry**: Count the flux coming from pixels within some circle at 
    specified coordinates. One of the main ways astronomers measure how bright 
    something is. Can be used to indicate that something is *not* detected to some 
    significance.
    
    **FXT**: Fast X-ray transient
    
    **GCN**: General Coordinates Network. A server for sending out notices of 
    interesting astrophysical phenomena, broadly used in the time-domain astronomy 
    community.

    **GRB**: Gamma-ray burst
    
    **GW**: Gravitational wave
    
    **kilonova (KN)**: Ultraviolet/optical/infrared transient powered by the radioactive 
    decay of freshly synthesized heavy elements. The expected electromagnetic 
    counterpart to *BNS* and *NSBH* events. 
    
    **kilonova-in-supernova (KN-in-SN)**: *Kilonova* embedded in a supernova. 
    Hypothesized electromagnetic counterpart to *SSM* events. Sometimes used 
    interchangably with *super-kilonova*.
    
    **LSST**: The Legacy Survey of Space and Time. The primary survey being conducted 
    by the Vera Rubin Observatory. 
    
    **neutrino**: Elementary particle (lepton) with no charge, which interacts via the 
    weak force. Produced in a variety of scenarios, such as a fusion in the core of the 
    Sun or in supernovae. Thought to be a signal in some multi-messenger events.
    
    **NLE**: Nonlocalized event. See *PLE*.
    
    **NSBH**: Neutron star + black hole merger
    
    **PLE**: Poorly localized event. A poorly localized event—also referred to as a 
    *nonlocalized event (NLE)*—is some astrophysical source with an uncertain position 
    on the sky. These can be large uncertainties of tens to thousands of square degrees 
    (e.g., gravitational waves) or a few square arcminutes (e.g., fast X-ray 
    transients.)
    
    **SSM**: Subsolar mass merger; merger involving at least one object subsolar in mass
    
    **supernova**: Explosive death of a star, either from thermonuclear detonation or 
    the collapse of a massive star.

    **super-kilonova (super-KN)**: Longer-lasting, "scaled up" *kilonova* 
    hypothesized to occur following the collapse of extremely massive (helium core in 
    excess of 100 solar masses), rapidly-rotating stars. Sometimes used interchangably 
    with *kilonova-in-supernova*. Hypothesized electromagnetic counterpart to *SSM* 
    events.
    
    **target**: An object defined by its name and position on the sky. Usually 
    accompanied by additional information like photometry or a redshift.
    
    **TNS**: The Transient Name Server. A repository of transients and their 
    photometry and spectroscopy. We continuously ingest transients from the TNS 
    to create *targets*.
    
    **ZTF**: Zwicky Transient Facility. A consortium of wide field-of-view optical 
    telescopes which survey a signifcant fraction of the night sky. We use the ZTF 
    forced photometry server to augment the lightcurves of *targets*.