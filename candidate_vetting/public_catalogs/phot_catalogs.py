"""
Code to query dynamically updating photometry catalogs
"""
# packages built into python
import os
import glob
import requests
import time
import json
import logging
import re
import csv
import math
import random
import string
import imaplib
import subprocess
import time
import email
from datetime import datetime
from operator import itemgetter
from collections import OrderedDict

# other packages
import numpy as np
import pandas as pd
from astropy.time import Time, TimezoneInfo
from astropy import units
from astropy.coordinates import SkyCoord
from fundamentals.stats import rolling_window_sigma_clip
from django.conf import settings
from django.db.models import F, FloatField, ExpressionWrapper
from django.db.models.functions import Sqrt

from .catalog import PhotCatalog
from .util import _QUERY_METHOD_DOCSTRING, RADIUS_ARCSEC, create_phot
from pyasassn.client import SkyPatrolClient

from trove_targets.models import Target

logger = logging.getLogger(__file__)

class TNS_Phot(PhotCatalog):
    """Query the TNS for the photometry they have available
    """

    def query(
            self,
            target:Target,
            timelimit:int = 10
    ):
        f"""Query the TNS for photometry they have available on this event

        Parameters
        ----------
        target : Target
            The target object to query TNS for. Usually don't include the prefix (AT, SN, TDE, etc.) except
            in the case of FRBs where the "FRB" prefix is necessary
        timelimit : int
            This is how long in seconds we are willing to wait for TNS to stop
            throttling our queries. Default is 10
        
        Returns
        -------
        True if we found more photometry and updated it, False if not
        """

        # get the tns base name
        # this will strip any letter prefix before the year
        tns_name = re.sub("^\D*", "", target.name)
        
        # parse some of the necessary info from settings.py
        BOT_ID = settings.BROKERS["TNS"]["bot_id"]
        BOT_NAME = settings.BROKERS["TNS"]["bot_name"]
        API_KEY = settings.TNS_API_KEY

        get_obj = [
            ("objname", tns_name),
            ("objid", ""),
            ("photometry", "1"),
            ("spectra", "0")
        ]
        
        # setup the query TNS for the photometry
        TNS="www.wis-tns.org"
        url_tns_api="https://"+TNS+"/api/get"
        get_url = url_tns_api + "/object"
        tns_marker = self._set_bot_tns_marker(BOT_ID, BOT_NAME)
        headers = {'User-Agent': tns_marker}
        json_file = OrderedDict(get_obj)
        get_data = {'api_key': API_KEY, 'data': json.dumps(json_file)}

        requests_kwargs = dict(
            headers = headers,
            data = get_data
        )

        logger.info(f"Posting the following request to {get_url}:\n{requests_kwargs}")
        response, time_to_reset = self._post_to_tns(
            get_url,
            requests_kwargs,
            timelimit
        )

        if response is None or response.status_code != 200:
            return False
        
        try:
            data = response.json()['data']
        except Exception as exc:
            logger.exception(f"Retrieving the TNS data failed with {exc}")
            return False
        
        return self._add_phot(target, data)        

    def _add_phot(self, target, tns_reply):
        """
        Add the data from the TNS response to the target 
        """
        
        # first check if we need to update the coordinates of this target
        tns_ra = float(f'{tns_reply["radeg"]:.14g}')
        tns_dec = float(f'{tns_reply["decdeg"]:.14g}')
        if target.ra != tns_ra or target.dec != tns_dec:
            target.ra = tns_ra
            target.dec = tns_dec
            target.save()
            logger.info(f'Updated coordinates to {target.ra:.6f}, {target.dec:.6f} based on TNS')

        # now we can ingest any new photometry
        n_new_phot = 0
        for candidate in tns_reply.get('photometry', []):
            jd = Time(candidate['jd'], format='jd', scale='utc')
            value = {'filter': candidate['filters']['name']}
            if candidate['flux']:  # detection
                value['magnitude'] = float(candidate['flux'])
            else:
                value['limit'] = float(candidate['limflux'])
            if candidate['fluxerr']:  # not empty or zero
                value['error'] = float(candidate['fluxerr'])
            created = create_phot(
                target = target,
                time = jd.to_datetime(timezone=TimezoneInfo()),
                fluxdict = value,
                source = candidate['telescope']['name'] + ' (TNS)'
            )

            n_new_phot += created
        if n_new_phot:
            logger.info(f'Added {n_new_phot:d} photometry points from the TNS')

        return bool(n_new_phot)
                
    def _post_to_tns(self, get_url, requests_kwargs, timelimit):

        # actually do the query and simulatanesouly
        # check the response to make sure it isn't throttling our API usage
        while True:

            response = requests.post(get_url, **requests_kwargs)
            try:
                logger.info(f"The TNS server responded with\n{response.json()}")
            except:
                logger.info(f"The TNS server responded with a response that can not be parsed as a JSON:\n{response}")

            if response.status_code != 200:
                return response, -99 # I'm just setting the time_to_reset to -99 so other code doesn't break

            remaining_str = response.headers.get('x-rate-limit-remaining', -99)
            time_to_reset = int(response.headers.get('x-rate-limit-reset', -99))  # in seconds

            if remaining_str == 'Exceeded':
                # we already exceeded the rate limit
                return None, time_to_reset

            remaining = int(remaining_str)

            if remaining < 0 or time_to_reset < 0:
                return response, time_to_reset

            if remaining == 0 and time_to_reset < timelimit:
                # we have no remaining API queries :(
                # but we don't have very long to wait!
                time.sleep(time_to_reset)

            elif remaining == 0 and time_to_reset > timelimit:
                # we are out of API queries :(
                # and it is gonna take to long to wait :((
                return None, time_to_reset

            else:
                return response, time_to_reset

    def _set_bot_tns_marker(self, BOT_ID: str = None, BOT_NAME: str = None):
        tns_marker = 'tns_marker{"tns_id": "' + str(BOT_ID) + '", "type": "bot", "name": "' + BOT_NAME + '"}'
        return tns_marker

class ASASSN_SkyPatrol(PhotCatalog):
    """ASASSN Forced photometry server
    """
    
    def query(
            self,
            ra:float,
            dec:float,
            radius:float=RADIUS_ARCSEC
    ):
        f"""Query the ASASSN SkyPatrol forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """
        client = SkyPatrolClient()
        light_curve = client.cone_search(
            ra_deg=ra,
            dec_deg=dec,
            radius=radius,
            units="arcsec",
            download=True,
            threads=self.nthreads,
        )

        return light_curve
    
class ATLAS_Forced_Phot(PhotCatalog):
    """ATLAS Forced photometry server
    """
    
    def query(
            self,
            target:Target,
            radius:float=RADIUS_ARCSEC,
            days_ago: float = 200.,
            token: str = None
    ):
        f"""Query the ATLAS forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """
        # get the RA and Dec from the target
        ra, dec = target.ra, target.dec
        
        _verbose = self._verbose
        
        BASEURL = "https://fallingstar-data.com/forcedphot"

        if token is None:
            try:
                token = settings.ATLAS_API_KEY
            except AttributeError:
                print('Setting token to environment variable ATLAS_API_KEY!')
                token = os.environ.get('ATLAS_API_KEY', None)
                
        if token is None:
            raise Exception('No token provided')
        else:
            print('Using provided token')

        headers = {'Authorization': f'Token {token}', 'Accept': 'application/json'}

        t_queryend = Time.now().mjd
        t_querystart = t_queryend - days_ago

        task_url = None
        while not task_url:
            with requests.Session() as s:
                resp = s.post(f"{BASEURL}/queue/", headers=headers, data={'ra': ra, 'dec': dec,
                                                                          'send_email': False,
                                                                          'mjd_min': t_querystart,
                                                                          'mjd_max': t_queryend,
                                                                          "use_reduced": False,})
                if resp.status_code == 201:  # success
                    task_url = resp.json()['url']
                    print(f'The task URL is {task_url}')
                elif resp.status_code == 429:  # throttled
                    message = resp.json()["detail"]
                    print(f'{resp.status_code} {message}')
                    t_sec = re.findall(r'available in (\d+) seconds', message)
                    t_min = re.findall(r'available in (\d+) minutes', message)
                    if t_sec:
                        waittime = int(t_sec[0])
                    elif t_min:
                        waittime = int(t_min[0]) * 60
                    else:
                        waittime = 10
                    print(f'Waiting {waittime} seconds')
                    time.sleep(waittime)
                else:
                    print(f'ERROR {resp.status_code}')
                    print(resp.json())
                    sys.exit()

        result_url = None
        taskstarted_printed = False
        while not result_url:
            with requests.Session() as s:
                resp = s.get(task_url, headers=headers)

                if resp.status_code == 200:  # HTTP OK
                    if resp.json()['finishtimestamp']:
                        result_url = resp.json()['result_url']
                        print(f"Task is complete with results available at {result_url}")
                    elif resp.json()['starttimestamp']:
                        if not taskstarted_printed:
                            print(f"Task is running (started at {resp.json()['starttimestamp']})")
                            taskstarted_printed = True
                        time.sleep(2)
                    else:
                        # print(f"Waiting for job to start (queued at {timestamp})")
                        time.sleep(4)
                else:
                    print(f'ERROR {resp.status_code}')
                    print(resp.text)
                    sys.exit()

        with requests.Session() as s:
            textdata = s.get(result_url, headers=headers).text

            # if we'll be making a lot of requests, keep the web queue from being
            # cluttered (and reduce server storage usage) by sending a delete operation
            # s.delete(task_url, headers=headers).json()

        ATLASphot = self._ATLAS_stack(textdata)

        # add the photometry to the target        
        return self._add_phot(target, ATLASphot)

    def _add_phot(self, target, data, signal_to_noise_cutoff = 3.0):        

        n_new_phot = 0
        for datum in data:
            time = Time(datum['mjd'], format='mjd')
            utc = TimezoneInfo(utc_offset=0*units.hour)
            time.format = 'datetime'
            value = {
                'filter': str(datum['F']),
                'telescope': 'ATLAS',
            }
            # If the signal is in the noise, calculate the non-detection limit from the reported flux uncertainty.
            # see https://fallingstar-data.com/forcedphot/resultdesc/
            signal_to_noise = datum['uJy'] / datum['duJy']
            if signal_to_noise <= signal_to_noise_cutoff:
                value['limit'] = 23.9 - 2.5 * np.log10(signal_to_noise_cutoff * datum['duJy'])
            else:
                value['magnitude'] = 23.9 - 2.5 * np.log10(datum['uJy'])
                value['error'] = 2.5 / np.log(10.) / signal_to_noise

            created = create_phot(
                target = target,
                time = time.to_datetime(timezone=utc),
                fluxdict = value,
                source = "ATLAS"
            )

            n_new_phot += created
        if n_new_phot:
            logger.info(f'Added {n_new_phot:d} photometry points from ATLAS forced photometry')

        return bool(n_new_phot)
            
        
    def _ATLAS_stack(self, filecontent):
        """
        Function adapted from David Young's :func:`plotter.plot_single_result`
        https://github.com/thespacedoctor/plot-results-from-atlas-force-photometry-service/blob/main/plot_atlas_fp.py
        """
        epochs = self._ATLAS_read_and_sigma_clip_data(filecontent, log=logger)

        # c = cyan, o = arange
        magnitudes = {
            'c': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
            'o': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
            'I': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
        }

        # SPLIT BY FILTER
        for epoch in epochs:
            if epoch["F"] in ["c", "o", "I"]:
                magnitudes[epoch["F"]]["mjds"].append(epoch["MJD"])
                magnitudes[epoch["F"]]["mags"].append(epoch["uJy"])
                magnitudes[epoch["F"]]["magErrs"].append(epoch["duJy"])
                magnitudes[epoch["F"]]['lim5sig'].append(epoch["mag5sig"])

        # STACK PHOTOMETRY IF REQUIRED
        stacked_magnitudes = self._stack_photometry(magnitudes, binningDays=1)
        
        return stacked_magnitudes

    def _ATLAS_read_and_sigma_clip_data(self, filecontent, log, clippingSigma=2.2):
        """
        Function adapted from David Young's :func:`plotter.read_and_sigma_clip_data`
        https://github.com/thespacedoctor/plot-results-from-atlas-force-photometry-service/blob/main/plot_atlas_fp.py

        *clean up rouge data from the files by performing some basic clipping*
        **Key Arguments:**
        - `fpFile` -- path to single force photometry file
        - `clippingSigma` -- the level at which to clip flux data
        **Return:**
        - `epochs` -- sigma clipped and cleaned epoch data
        """

        # CLEAN UP FILE FOR EASIER READING
        fpData = filecontent.replace("###", "").replace(" ", ",").replace(
            ",,", ",").replace(",,", ",").replace(",,", ",").replace(",,", ",").splitlines()

        # PARSE DATA WITH SOME FIXED CLIPPING
        oepochs = []
        cepochs = []
        csvReader = csv.DictReader(
            fpData, dialect='excel', delimiter=',', quotechar='"')

        for row in csvReader:
            for k, v in row.items():
                try:
                    row[k] = float(v)
                except:
                    pass
            # REMOVE VERY HIGH ERROR DATA POINTS, POOR CHI SQUARED, OR POOR EPOCHS
            if row["duJy"] > 4000 or row["chi/N"] > 100 or row['mag5sig']<17.:
                continue
            if row["F"] == "c":
                cepochs.append(row)
            if row["F"] == "o":
                oepochs.append(row)

        # SORT BY MJD
        cepochs = sorted(cepochs, key=itemgetter('MJD'), reverse=False)
        oepochs = sorted(oepochs, key=itemgetter('MJD'), reverse=False)

        # SIGMA-CLIP THE DATA WITH A ROLLING WINDOW
        cdataFlux = []
        cdataFlux[:] = [row["uJy"] for row in cepochs]
        odataFlux = []
        odataFlux[:] = [row["uJy"] for row in oepochs]

        maskList = []
        for flux in [cdataFlux, odataFlux]:
            fullMask = rolling_window_sigma_clip(
                log=log,
                array=flux,
                clippingSigma=clippingSigma,
                windowSize=11)
            maskList.append(fullMask)

        try:
            cepochs = [e for e, m in zip(
                cepochs, maskList[0]) if m == False]
        except:
            cepochs = []

        try:
            oepochs = [e for e, m in zip(
                oepochs, maskList[1]) if m == False]
        except:
            oepochs = []

        print('Completed the ``read_and_sigma_clip_data`` function')
        # Returns ordered dictionary of all parameters
        return cepochs + oepochs

    def _stack_photometry(self, magnitudes, binningDays=1.):
        # IF WE WANT TO 'STACK' THE PHOTOMETRY
        summedMagnitudes = {
            'c': {'mjds': [], 'mags': [], 'magErrs': [], 'n': [], 'lim5sig': []},
            'o': {'mjds': [], 'mags': [], 'magErrs': [], 'n': [], 'lim5sig': []},
            'I': {'mjds': [], 'mags': [], 'magErrs': [], 'n': [], 'lim5sig': []},
        }

        # MAGNITUDES/FLUXES ARE DIVIDED IN UNIQUE FILTER SETS - SO ITERATE OVER
        # FILTERS
        allData = []
        for fil, data in list(magnitudes.items()):
            # WE'RE GOING TO CREATE FURTHER SUBSETS FOR EACH UNQIUE MJD (FLOORED TO AN INTEGER)
            # MAG VARIABLE == FLUX (JUST TO CONFUSE YOU)
            distinctMjds = {}
            for mjd, flx, err, lim in zip(data["mjds"], data["mags"], data["magErrs"], data["lim5sig"]):
                # DICT KEY IS THE UNIQUE INTEGER MJD
                key = str(int(math.floor(mjd / float(binningDays))))
                # FIRST DATA POINT OF THE NIGHTS? CREATE NEW DATA SET
                if key not in distinctMjds:
                    distinctMjds[key] = {
                        "mjds": [mjd],
                        "mags": [flx],
                        "magErrs": [err],
                        "lim5sig": [lim]
                    }
                # OR NOT THE FIRST? APPEND TO ALREADY CREATED LIST
                else:
                    distinctMjds[key]["mjds"].append(mjd)
                    distinctMjds[key]["mags"].append(flx)
                    distinctMjds[key]["magErrs"].append(err)
                    distinctMjds[key]["lim5sig"].append(lim)

            # ALL DATA NOW IN MJD SUBSETS. SO FOR EACH SUBSET (I.E. INDIVIDUAL
            # NIGHTS) ...
            for k, v in list(distinctMjds.items()):
                # GIVE ME THE MEAN MJD
                meanMjd = sum(v["mjds"]) / len(v["mjds"])

                # GIVE ME THE MEAN FLUX
                meanFLux = sum(v["mags"]) / len(v["mags"])

                # GIVE ME THE COMBINED ERROR
                sum_of_squares = sum(x ** 2 for x in v["magErrs"])
                combError = math.sqrt(sum_of_squares) / len(v["magErrs"])

                # 5-sigma limits
                try:
                    comb5SigLimit = 23.9 - 2.5 * math.log10(5. * combError)
                except ValueError:
                    logger.warn("Skipping this ATLAS photometry point because math.log10 raises a domain error!")
                    continue # this skips to the next for-loop iteration
                    
                # GIVE ME NUMBER OF DATA POINTS COMBINED
                n = len(v["mjds"])
                
                summedMagnitudes[fil]["mjds"].append(meanMjd)
                summedMagnitudes[fil]["mags"].append(meanFLux)
                summedMagnitudes[fil]["magErrs"].append(combError)
                summedMagnitudes[fil]["lim5sig"].append(comb5SigLimit)
                summedMagnitudes[fil]["n"].append(n)

                allData.append({
                    'mjd': meanMjd,
                    'uJy': meanFLux,
                    'duJy': combError,
                    'F': fil,
                    'n': n,
                    'mag5sig': comb5SigLimit
                })
        print('completed the ``stack_photometry`` method')

        return allData
    
class ZTF_Forced_Phot(PhotCatalog):
    """ZTF Forced photometry server
    
    Most of this code was yoinked from Dave Coulter's YSE PZ code:
    https://github.com/davecoulter/YSE_PZ/blob/58f3e6a1622ec5755e5322aee2d00f3941510749/YSE_App/data_ingest/ZTF_Forced_Phot.py

    Noah: I'm hacking it together from there to resemble e.g., the ATLAS_Forced_Phot.query
    function call
    """

    def __init__(self):

        self._generic_ztfuser = "ztffps"
        self._generic_ztfinfo = "dontgocrazy!"
        
        self._ztffp_email_address = settings.ZTF_INFO.get("email_address")
        self._ztffp_email_password = settings.ZTF_INFO.get("email_password")
        self._ztffp_email_server = settings.ZTF_INFO.get("email_server")
        self._ztffp_user_address = settings.ZTF_INFO.get("user_address")
        self._ztffp_user_password = settings.ZTF_INFO.get("user_password")
        
        super().__init__("ZTF Forced Photometry")
        
    def query(
            self,
            target:Target,
            radius:float=RADIUS_ARCSEC,
            days_ago: float = 200,
            wait_for_results = False,
            max_wait_time = 24*60*60, # default to a day long max wait time (in seconds)
            dt_wait_time = 5*60, # wait 5 minutes between every email query
    ):
        f"""Query the ZTF forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """

        ztf_logs = self._ztf_forced_photometry(
            target.ra,
            target.dec,
            days=days_ago
        )

        if not wait_for_results:
            return
        
        ztf_forced_phot_file = None
        start_time = time.time()
        total_time_waited = 0
        while ztf_forced_phot_file is None:
            # wait for the ZTF email to arrive
            ztf_forced_phot_file = self._query_ztf_email(ztf_logs)
            
            # wait the "dt_wait_time" before trying again
            time.sleep(dt_wait_time)

            total_time_waited += dt_wait_time

            if total_time_waited > max_wait_time:
                break

    def check_for_new_data(self):
        """Check the TROVE gmail for new ZTF forced photometry, parse it, and upload it"""

        # get a list of all input log files
        logfiles = glob.glob(os.path.join(settings.ZTFTMPDIR,"*.txt"))

        # search for emails matching those logfiles, download the data, and save it
        targets_finished = []
        for logfile in logfiles:
            result = self._query_ztf_email(logfile)
            if result is None:
                continue

            # unpack the files
            fplc, fplog = result

            ra, dec = self._read_fp_log(fplog)
            logger.info(f"Finding TROVE Target at ra={ra} dec={dec} to add this photometry to")

            # this coordinate should be within 0.1" of the actual target coordinates
            # because we've rounded the ra/dec in degrees to 6 decimal places
            # since that's what ZTF accepts
            targ = Target.objects.annotate(
                onskydist = ExpressionWrapper(
                    Sqrt(
                        (F('ra') - ra)**2 + (F('dec') - dec)**2
                    ),
                    output_field = FloatField()
                )
            ).filter(
                onskydist__lt = 0.1/3600 
            ).order_by("onskydist").first()
            logger.info(f"Found {targ.name} at ra={ra} dec={dec}")
            
            # now we can also parse the photometry
            phot = pd.read_csv(fplc, sep="\s+", comment="#")
            phot.columns = phot.columns.str.replace(",","") # bc the column names have commas but not the data
            
            # save this photometry to the database
            phot = self._filter_bad_phot(phot)
            self._save_phot(targ, phot)
            
            # cleanup
            os.remove(fplc) # rm the light curve file
            os.remove(fplog) # rm the light curve log
            os.remove(logfile) # rm the log file associated with the original request so we don't keep checking for it

            logger.info(f"Finished ingesting ZTF forced photometry for {targ.name}")
            targets_finished.append(targ.name)
            
        return targets_finished

    def _filter_bad_phot(self, phot):
        """This is based on the recommendations in sec.6 of the ZTF FP docs"""
        
        # 1. Remove epochs with infobitssci >= 33554432
        # 2. Remove epochs with scisigpix > 25 
        # 3. Remove epochs with sciinpseeing > 4"
        phot = phot[
            (phot.infobitssci < 33554432) *
            (phot.scisigpix <= 25) *
            (phot.sciinpseeing <= 4)
        ]

        # then we also need to check the flux uncertainty estimates
        # according to sec. 6.3 in the docs
        # essentially, chisq should be roughly 1, and if it isn't then we should
        # correct the flux uncertainty
        mask = (phot.forcediffimchisq > 0.9) * (phot.forcediffimchisq < 1.1)
        phot.loc[mask, "forcediffimfluxunc"] = phot.loc[mask, "forcediffimfluxunc"] * np.sqrt(phot.loc[mask, "forcediffimchisq"])

        return phot
        
    def _save_phot(self, targ, phot, snr_thresh=3, snr_limit=5):
        """save the photometry to targ

        snr_thresh and snr_limit are directly from sec 6.3 of the ZTF FP docs
        """

        for _,row in phot.iterrows():
            time = Time(row.jd, format="jd", scale="utc")

            value = {
                'filter':row["filter"].replace("ZTF_",""),
                'telescope':"ZTF"
            }

            flux = float(row.forcediffimflux)
            flux_err = float(row.forcediffimfluxunc)
            flux_zp = float(row.zpdiff)
            snr = flux / flux_err
            
            if snr > snr_thresh:
                # this should be considered a detection
                value["magnitude"] = flux_zp - 2.5*np.log10(flux)
                value["error"] = 2.5/np.log(10)/snr
            else:
                # this should be considered a limit
                value["limit"] = flux_zp - 2.5*np.log10(snr_limit*flux_err)

            created = create_phot(
                target = targ,
                time = time.to_datetime(timezone=TimezoneInfo()),
                fluxdict = value,
                source = "ZTF FP"
            )
        
    def _query_ztf_email(self, log_file_name, source_name=None):
        """This checks the trove email address for new emails from ZTF withd dataproducts"""

        downloaded_file_names = None

        if not os.path.exists(log_file_name):

            print("%s does not exist."%log_file_name)
            return


        # Interpret the request sent to the ZTF forced photometry server
        job_info = self._read_job_log(log_file_name)

        try:

            imap = imaplib.IMAP4_SSL(self._ztffp_email_server)
            imap.login(self._ztffp_email_address, self._ztffp_email_password)

            status, messages = imap.select("INBOX")
            processing_match = False
            # if it's not in the first 100 messages, then I don't care
            for i in range(int(messages[0]), 0, -1)[0:100]:
                if processing_match:
                    break

                # Fetch the email message by ID
                res, msg = imap.fetch(str(i), "(RFC822)")
                for response in msg:
                    if not isinstance(response, tuple):
                        continue
                    
                    # Parse a bytes email into a message object
                    msg = email.message_from_bytes(response[1])
                    # decode the email subject
                    sender, encoding = email.header.decode_header(msg.get("From"))[0]

                    if (
                            isinstance(sender, bytes) or
                            not re.search("ztfpo@ipac\.caltech\.edu", sender)
                    ): continue # move onto the next email
                    
                    # Get message body
                    content_type = msg.get_content_type()
                    body = msg.get_payload(decode=True).decode()

                    this_date = msg['Date']
                    this_date_tuple = email.utils.parsedate_tz(msg['Date'])
                    local_date = datetime.fromtimestamp(email.utils.mktime_tz(this_date_tuple))

                    # Check if this is the correct one
                    if not content_type=="text/plain":
                        continue # move onto the next email 

                    processing_match = self._match_ztf_message(job_info, body, local_date)
                    subject, encoding = email.header.decode_header(msg.get("Subject"))[0]

                    if not processing_match:
                        continue # move onto the next email

                    # Grab the appropriate URLs
                    lc_url = 'https' + (body.split('_lc.txt')[0] + '_lc.txt').split('https')[-1]
                    log_url = 'https' + (body.split('_log.txt')[0] + '_log.txt').split('https')[-1]


                    # Download each file
                    lc_initial_file_name = self._download_ztf_url(lc_url)
                    log_initial_file_name = self._download_ztf_url(log_url)


                    # Rename
                    if source_name is None:
                        downloaded_file_names = [lc_initial_file_name, log_initial_file_name]
                    else:
                        lc_final_name = source_name.replace(' ','')+"_"+lc_initial_file_name.split('_')[-1]
                        log_final_name = source_name.replace(' ','')+"_"+log_initial_file_name.split('_')[-1]
                        os.rename(lc_initial_file_name, lc_final_name)
                        os.rename(log_initial_file_name, log_final_name)
                        downloaded_file_names = [lc_final_name, log_final_name]

            imap.close()
            imap.logout()

        # Connection could be broken
        except Exception as e:
            logger.exception(e)
            pass

        if downloaded_file_names is not None:

            for file_name in downloaded_file_names:
                logger.info("Downloaded: %s"%file_name)

        return downloaded_file_names

    def _ztf_forced_photometry(self,ra, decl, jdstart=None, jdend=None, days=60, send=True):
        """Start the ZTF forced photometry job"""
        
        if jdend is None:
            jdend = Time(datetime.utcnow(), scale='utc').jd

        if jdstart is None:
            jdstart = jdend - days

        if ra is None or decl is None:
            raise RuntimeError("Missing necessary R.A. or declination.")

        # convert ra and decl to a skycoord object
        skycoord = SkyCoord(ra, decl, frame='icrs', unit='deg')
        
        # Convert to string to keep same precision. This will make matching easier
        # in the case of submitting multiple jobs.
        jdend_str = np.format_float_positional(float(jdend), precision=6)
        jdstart_str = np.format_float_positional(float(jdstart), precision=6)
        ra_str = np.format_float_positional(float(skycoord.ra.deg), precision=6)
        decl_str = np.format_float_positional(float(skycoord.dec.deg), precision=6)

        log_file_name = self._random_log_file_name(log_file_dir=settings.ZTFTMPDIR) # Unique file name

        logger.info("Sending ZTF request for (R.A.,Decl)=(%s,%s)"%(ra,decl))

        wget_command = "wget --http-user=%s --http-passwd=%s -O %s \"https://ztfweb.ipac.caltech.edu/cgi-bin/requestForcedPhotometry.cgi?"%(self._generic_ztfuser,self._generic_ztfinfo,log_file_name) + \
                       "ra=%s&"%ra_str + \
                       "dec=%s&"%decl_str + \
                       "jdstart=%s&"%jdstart_str +\
                       "jdend=%s&"%jdend_str + \
                       "email=%s&userpass=%s\""%(self._ztffp_user_address,self._ztffp_user_password)
        logger.info(wget_command)

        if send:
            p = subprocess.Popen(wget_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
            stdout, stderr = p.communicate()

        os.chmod(log_file_name,0o0777)
        return log_file_name

    def _random_log_file_name(self, log_file_dir='/tmp'):

        log_file_name = None
        while log_file_name is None or os.path.exists(log_file_name):
            log_file_name = f"{log_file_dir}/ztffp_%s.txt"%''.join(
                [
                    random.choice(
                        string.ascii_uppercase + string.digits
                    ) for i in range(10)
                ]
            )
            
        return log_file_name

    def _download_ztf_url(self, url):

        wget_command = "wget --http-user=%s --http-password=%s -O %s \"%s\""%(
            self._generic_ztfuser,
            self._generic_ztfinfo,
            url.split('/')[-1],
            url
        )

        logger.info("Downloading file...")
        logger.info('\t' + wget_command)

        p = subprocess.Popen(wget_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        stdout, stderr = p.communicate()

        return url.split('/')[-1]

    def _match_ztf_message(self, job_info, message_body, message_time_epoch):

        match = False

        message_lines = message_body.splitlines()

        for line in message_lines:
            if re.search("reqid", line):

                inputs = line.split('(')[-1]
                
                # Two ways
                # Processing has completed for reqid=XXXX ()
                test_ra = inputs.split('ra=')[-1].split(',')[0]
                test_decl = inputs.split('dec=')[-1].split(')')[0]
                if re.search('minJD', line) and re.search('maxJD', line):
                    test_minjd = inputs.split('minJD=')[-1].split(',')[0]
                    test_maxjd = inputs.split('maxJD=')[-1].split(',')[0]
                else:
                    test_minjd = inputs.split('startJD=')[-1].split(',')[0]
                    test_maxjd = inputs.split('endJD=')[-1].split(',')[0]

                # Call this a match only if parameters match
                if np.format_float_positional(float(test_ra), precision=6, pad_right=6).replace(' ','0') == job_info['ra'].to_list()[0] and \
                   np.format_float_positional(float(test_decl), precision=6, pad_right=6).replace(' ','0') == job_info['dec'].to_list()[0] and \
                   np.format_float_positional(float(test_minjd), precision=6, pad_right=6).replace(' ','0') == job_info['jdstart'].to_list()[0] and \
                   np.format_float_positional(float(test_maxjd), precision=6, pad_right=6).replace(' ','0') == job_info['jdend'].to_list()[0]:

                   match = True

        return match

    def _read_job_log(self, file_name):

        job_info = pd.read_html(file_name)[0]
        job_info['ra'] = np.format_float_positional(float(job_info['ra'].to_list()[0]), precision=6, pad_right=6).replace(' ','0')
        job_info['dec'] = np.format_float_positional(float(job_info['dec'].to_list()[0]), precision=6, pad_right=6).replace(' ','0')
        job_info['jdstart'] = np.format_float_positional(float(job_info['jdstart'].to_list()[0]), precision=6, pad_right=6).replace(' ','0')
        job_info['jdend'] = np.format_float_positional(float(job_info['jdend'].to_list()[0]), precision=6, pad_right=6).replace(' ','0')
        job_info['isostart'] = Time(float(job_info['jdstart'].to_list()[0]), format='jd', scale='utc').iso
        job_info['isoend'] = Time(float(job_info['jdend'].to_list()[0]), format='jd', scale='utc').iso
        job_info['ctime'] = os.path.getctime(file_name) - time.localtime().tm_gmtoff
        job_info['cdatetime'] = datetime.fromtimestamp(os.path.getctime(file_name))

        return job_info

    def _read_fp_log(self, filename):

        with open(filename, "r") as f:
            loglines = f.readlines()

        ra = float(loglines[6].replace("fph_ra = ", "").replace("degrees", "").strip())
        dec = float(loglines[7].replace("fph_dec = ", "").replace("degrees", "").strip())

        return ra, dec
