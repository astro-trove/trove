Web App Details
===============

Getting Started
---------------

TROVE is located at https://datatrove.as.arizona.edu. When you first arrive at the page 
you will see the home page, with no content, and you should either register for an 
account or login. If you are registering for the first time, it may take 1-2 days for 
your account to be approved by an administrator. If it has not been approved in that 
time, please post a help message in the Slack workspace.

Before logging in the home page should look like the following, with little shown other
than some recent events of interest:

.. image:: webappimg/home-loggedout.png

|

After logging in you should be able to see more, including latest comments:

.. image:: webappimg/home-loggedin.png

|

From here, take a look at the options in the navigation bar. The search bar at right 
can be used to search for either events or candidate counterparts to events.


Viewing Poorly Localized ("Nonlocalized") Events
------------------------------------------------

A poorly localized event (PLE)---sometimes referred to as a nonlocalized event 
(NLE), for example within the TOMToolkit package---is some astrophysical source with an 
uncertain position on the sky. These can be large uncertainties of tens to thousands of 
square degrees (e.g., gravitational waves; GWs) or a few square arcminutes (e.g., fast 
X-ray transients; FXTs)

First, click on the **Events** tab dropdown in the navigation bar. From the dropdown 
menu, select **Gravitational Waves** to go the following page:

.. image:: webappimg/ple-gw.png

|

You'll see a list of all GW events that we have ingested into our 
database. By default, we filter the table to only include events with an false alarm 
rate (FAR) < 0.5 per year (i.e., once per two years; the recommendation from the 
International Gravitational-Wave Observatory Network IGWN). You can adjust this FAR 
limit using the form at the top. There are also various other options that you are able 
to filter on using the form at the top of the page.

From this table, you are able to access the candidates for a specific GW event by 
clicking on its name from the table. After clicking on a GW event you should see a page 
like:

.. image:: webappimg/candidate-top.png

|

This section shows some basic information on the GW event and a skymap with the 
candidates plotted on top of the GW localization.

Scrolling down, you should now see a table of the candidates ranked by score. For 
example:

.. image:: webappimg/candidate-table.png

|

Depending on the type of event, you will see different score columns. For a binary 
neutron star (BNS) merger or neutron star - black hole (NSBH) merger, only the kilonova 
(KN) score will be displayed. For a subsolar mass merger (SSM) event like S251112cm 
above, you'll see KN, kilonova-in-supernova (KN-in-SN), and super-kilonova (super-KN) 
scores.

You'll immediately notice a few buttons:

- Export a CSV of selected candidates and their coordinates, latest detections, 
  potential host galaxies, etc. by clicking **Export Selected**. This may be helpful for 
  putting in target coordinates into some observing software for planning or executing 
  observations.
- Submit a job to asynchronously vet and re-score selected candidates by clicking 
  **Vet Selected**. You may vet up to 20 at once.
- Submit a job to asynchronously vet and re-score everything, use the **Vet All** button. 
  Note however that the scoring will may take ~a second per candidate, which can add up 
  to ~minutes-hours for GW events with many candidates. This button is therefore on a 
  global cooldown for all users. Use **Vet Selected** instead whenever possible.

You'll also see some filters. The simplest filter is to filter by candidate name, but 
you can also filter by, e.g., the maximum distance you're interested in:

.. image:: webappimg/candidate-table-filters.png

|


In the right column above the table, there is also a box to search for a single 
candidate by name and associate it with the event---though candidates should be linked 
automatically! 

Finally, note also the toggle-able buttons for (1) including the AGN subscore in 
computing the overall score and (2) whether to use simple light curve metrics or 
``KilonovaSCORER`` to compute the photometry subscore for kilonovae, when applicable.

From this page, the normal workflow is to click on specific candidates and look at 
details. We will go into details about targets in the following sections.


Target / Candidate Table
------------------------
In addition to accessing candidates via the PLEs to which they are associated, we can 
also search for targets which may or may not be associated with an event. (We use the 
term target to generically refer to a well-localized astrophysical source, and 
candidate once that target has been associated with a PLE). From the navigation bar at 
top, clicking **EM Candidates** and selecting **All Candidates** from the dropdown menu, 
you will be led to the following page:

.. image:: webappimg/target-table.png

|

Where you'll see a table of targetss with the most recently ingested, edited, and 
discovered near the top. This table will indicate the first detection and distance to 
the target, if known, and any associated events. Just like on the event candidates 
page, there are useful filters to apply here as needed and you can click individual 
targets to go to their page.


Target / Candidate Page
-----------------------
This page contains the details of a single target (candidate, if associated with an 
event). You should see:

.. image:: webappimg/target.png

|

Things like coordinates, name aliases, redshift (if known), and extinction are all 
shown on the left sidebar. At top, see buttons for:

1. Update Lightcurve: Submit a job to obtain forced photometry from ATLAS or ZTF.
2. Update Host z: Submit a spectroscopic redshift measurement for one of the host 
   galaxies in the galaxy table. (See: `Submitting Galaxy Spec-z's <updatingredshifts.html>`_)
3. Vet: Vet the target / candidate.

On the right, in the Score Details tab, see details of the subscores that are general 
to the target, and others specific to a PLE-target pair. For example, crossmatching 
with point sources and solar system objects from the Minor Planet Center (MPC) is 
possible for all targets, while other scores (2D localization, distance, and 
photometry) are dependent on the PLE and the class of transient you are vetting in 
search of.

Below the score details is a table of potential host galaxies. These are determined 
based on a probability of chance coincidence (:math:`P_{\rm cc}`) calculation, where a 
lower :math:`P_{cc}` indicates a more likely host galaxy. In TROVE we make a cut on 
:math:`P_{\rm cc} < 0.15`, so only low-:math:`P_{cc}` galaxies should appear in this 
table. The distances, redshifts, and type of distance measurement 
(redshift-independent, spectroscopic redshift, photometric redshift) of these 
individual host galaxies are also shown in this table. 

An atlas viewer at bottom left shows the position of the target (red circle) and these 
potential hosts (pink circle):

.. image:: webappimg/target-atlas.png

|

Next, click the **Photometry** tab to see the lightcurves: 

.. image:: webappimg/target-photometry.png

|

Playing with the filters on bands, sources (telescopes) and minimum SNR will affect 
both the plotted lightcurve and the photometry table.

Finally, click the **Comments** tab to view or submit a comment to share with the 
community. A comment could include some plan to observe a candidate or its potential 
host. 

This is just a brief overview of the pages most users will access in the web app. Dive 
in to the TROVE to see for yourself!