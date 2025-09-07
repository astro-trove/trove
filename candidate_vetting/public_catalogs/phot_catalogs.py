"""
Code to query dynamically updating photometry catalogs
"""
import os
import requests
import time
import json
import logging
import re
import csv
import math
from operator import itemgetter
from collections import OrderedDict

import numpy as np
from astropy.time import Time, TimezoneInfo
from astropy import units
from fundamentals.stats import rolling_window_sigma_clip
from django.conf import settings

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
        f"""Query the ZTF forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """

        # get the RA and Dec from the target
        ra, dec = target.ra, target.dec
        
        _verbose = self._verbose
        
        BASEURL = "https://fallingstar-data.com/forcedphot"

        if token is None:
            token = settings.ATLAS_API_KEY
        else:
            token = os.environ.get('ATLAS_API_KEY', None)

        if token is None:
            raise Exception('No token provided')
        else:
            print('Using token from environment')

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
                summedMagnitudes[fil]["mjds"].append(meanMjd)
                # GIVE ME THE MEAN FLUX
                meanFLux = sum(v["mags"]) / len(v["mags"])
                summedMagnitudes[fil]["mags"].append(meanFLux)
                # GIVE ME THE COMBINED ERROR
                sum_of_squares = sum(x ** 2 for x in v["magErrs"])
                combError = math.sqrt(sum_of_squares) / len(v["magErrs"])
                summedMagnitudes[fil]["magErrs"].append(combError)
                # 5-sigma limits
                comb5SigLimit = 23.9 - 2.5 * math.log10(5. * combError)
                summedMagnitudes[fil]["lim5sig"].append(comb5SigLimit)
                # GIVE ME NUMBER OF DATA POINTS COMBINED
                n = len(v["mjds"])
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
    """

    def query(
            self,
            ra:float,
            dec:float,
            radius:float = RADIUS_ARCSEC
    ):
        f"""Query the ZTF forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """
        pass

