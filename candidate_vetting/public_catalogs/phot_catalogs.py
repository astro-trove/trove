"""
Code to query dynamically updating photometry catalogs
"""
import os

from astropy.time import Time
from fundamentals.stats import rolling_window_sigma_clip
from django.conf import settings

from .catalog import PhotCatalog
from .util import _QUERY_METHOD_DOCSTRING, RADIUS_ARCSEC
from pyasassn.client import SkyPatrolClient

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
            ra:float,
            dec:float,
            radius:float=RADIUS_ARCSEC,
            days_ago: float = 200.,
            token: str = None
    ):
        f"""Query the ZTF forced photometry service

        {_QUERY_METHOD_DOCSTRING}
        """

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
                resp = s.post(f"{BASEURL}/queue/", headers=headers, data={'ra': RA, 'dec': Dec,
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

        return ATLASphot

    def _ATLAS_stack(self, textdata):
        """
        Function adapted from David Young's :func:`plotter.plot_single_result`
        https://github.com/thespacedoctor/plot-results-from-atlas-force-photometry-service/blob/main/plot_atlas_fp.py
        """
        epochs = self._ATLAS_read_and_sigma_clip_data(filecontent, log=log)

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

    def _ATLAS_read_and_sigma_clip_data(filecontent, log, clippingSigma=2.2):
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

    def _stack_photometry(magnitudes, binningDays=1.):
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

