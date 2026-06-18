Web App Details
===============

Getting Started
---------------

TROVE is located at https://datatrove.as.arizona.edu. When you first arrive at the page you will see the home page, with no
content, and you should either register for an account or login. If you are registering for the first time, it may take 1-2
days for your account to be approved by an administrator. If it has not been approved in that time, please post a help message in the
Slack workspace.

Before logging in the home page should look like the following, with no targets or comments shown:

.. image:: webappimg/home-loggedout.png

|

After logging in you should be able to see a list of targets and comments:

.. image:: webappimg/home-loggedin.png


Viewing Nonlocalized Events
---------------------------

A nonlocalized event (NLE)---sometimes more aptly termed a poorly localized event---is some astrophysical source with an 
uncertain position on the sky. These can be large uncertainties of tens to thousands of square degrees (e.g., gravitational waves)
or tens of square arcminutes (e.g., fast X-ray transients.)

First, click on the "Nonlocalized Events" tab dropdown in the navigation bar (navbar). From the dropdown menu, select 
"Gravitational Waves" to go the following page:

.. image:: webappimg/nle-gw.png

|

On this page, you will see a list of all gravitational wave (GW) events that we have ingested into our database from the IGWN. By 
default, we filter the table to only include events with an false alarm rate (FAR) < 0.5 per year (i.e., once per two years) 
(the recommendation from the International Gravitational-Wave Observatory Network, IGWN). You can adjust this FAR 
limit using the form at the top. There are also various other options that you are able to filter on using the form at the top 
of the page.

From this table, you are able to access the candidates for a specific GW event by clicking on its name from the table. After
clicking on a GW event you should see a page like:

.. image:: webappimg/candidate-top.png

|

This section shows (1) some basic information on the GW event and (2) a skymap with the candidates plotted on top of the
GW localization.

Scrolling down you should now see a table of the candidates ranked by score. For example:

.. image:: webappimg/candidate-table.png

|

Depending on the type of event, you will see different types of scores in the "score" column. For a binary neutron star (BNS) merger 
or neutron star - black hole (NSBH) merger, only the kilonova (KN) score will be displayed. For a subsolar mass merger (SSM) event like 
S251112cm above, you'll see KN, kilonova-in-supernova (KN-in-SN), and super-kilonova (super-KN) scores.

At the top of this table you can search by candidate name and link new candidates if something is missing (although the candidates
should be linked automatically!). To asynchronously rerun forced photometry and re-score everything, use the "Vet All" button. 
Note however that the scoring will take a few seconds per candidate, which can add up to ~minutes-hours for GW events with many 
candidates. Forced photometry will take ~hours-days. It is usually best practice to re-vet the candidates of interest.

From this page, the normal workflow is to click on specific candidates and look at details. We will go into details about targets
in the next two sections.

Target List Page
----------------
In addition to accessing candidates via NLEs to which they are associated, we can also search for "Targets" which 
may or may not be associated with a nonlocalized event. This will look something like:

.. image:: webappimg/target-top.png

|

Scrolling down, you will see a table of targets with the most recently ingested, edited, and discovered targets near the top:

.. image:: webappimg/target-table.png

|

From this page, just like on the candidate page, you can click individual targets and go to their page.

The Target Page
---------------
This page contains details of the targets themselves. When first coming to a target page it will look like:

.. image:: webappimg/target-host-table.png

|

Things like coordinates, name aliases, redshift (if known), and extinction are all shown on the left sidebar. At top, 
see buttons for:

1. Update Lightcurve: Submit a job to obtain forced photometry from ATLAS or ZTF.
2. Update Host z: Submit a spectroscopic redshift measurement for one of the host galaxies in the galaxy table.

On the right, see details of the subscores that are general to the target, and others specific to a 
NLE-target pair. For example, crossmatching with point sources, asteroids from the Minor Planet Center (MPC), and AGN 
is possible for all targets, while other scores (2D localization, distance, and photometry) are dependent on the NLE. 

Below the score details is a table of potential host galaxies. These are determined based on a probability of chance
coincidence calculation (Pcc), where a lower Pcc indicates a more likely host galaxy. In TROVE we make a cut on Pcc < 0.15,
so only low Pcc galaxies should appear in this table. The distances, redshifts, and type of distance measurement 
(redshift-independent, spectroscopic redshift, photometric redshift) of these individual host galaxies are also
shown in this table. An atlas viewer at bottom left shows the position of the target and these potential hosts:

.. image:: webappimg/target-atlas.png

|

Finally, on the right side below the host galaxy table is the light curve, including photometry from TNS and forced photometry
servers. This light curve looks like the following:

.. image:: webappimg/target-photometry.png
