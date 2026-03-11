"""
Code to query dynamically updating photometry catalogs
"""
import csv
import math
import os
import re
import sys
import time
from operator import itemgetter

import numpy as np
import requests
from astropy.time import Time
from django.conf import settings

from .catalog import PhotCatalog
from .util import _QUERY_METHOD_DOCSTRING, RADIUS_ARCSEC


def _rolling_window_sigma_clip(array, clipping_sigma=2.2, window_size=11):
    """
    Perform sigma clipping using a rolling window.
    
    Replacement for fundamentals.stats.rolling_window_sigma_clip.
    Uses numpy for vectorized operations.
    
    Parameters
    ----------
    array : array-like
        Input data array
    clipping_sigma : float
        Number of standard deviations for clipping threshold
    window_size : int
        Size of the rolling window (must be odd)
    
    Returns
    -------
    mask : np.ndarray
        Boolean mask where True indicates clipped (bad) values
    """
    array = np.asarray(array, dtype=float)
    n = len(array)
    
    if n == 0:
        return np.array([], dtype=bool)
    
    if n < window_size:
        window_size = max(3, n if n % 2 == 1 else n - 1)
    
    half_window = window_size // 2
    mask = np.zeros(n, dtype=bool)
    
    for i in range(n):
        start = max(0, i - half_window)
        end = min(n, i + half_window + 1)
        window = array[start:end]
        
        if len(window) < 3:
            continue
            
        median = np.nanmedian(window)
        std = np.nanstd(window)
        
        if std > 0:
            deviation = abs(array[i] - median)
            if deviation > clipping_sigma * std:
                mask[i] = True
    
    return mask


class ASASSN_SkyPatrol(PhotCatalog):
    """ASASSN SkyPatrol forced photometry service.
    
    Queries the ASASSN SkyPatrol API directly without requiring pyasassn.
    API documentation: https://asas-sn.osu.edu/
    """
    
    ASASSN_API_URL = "https://asas-sn.osu.edu/api/v1/light_curves"
    
    def query(
            self,
            ra: float,
            dec: float,
            radius: float = RADIUS_ARCSEC
    ):
        f"""Query the ASASSN SkyPatrol forced photometry service.

        {_QUERY_METHOD_DOCSTRING}
        
        Returns
        -------
        dict or None
            Light curve data from ASASSN, or None if query fails
        """
        try:
            params = {
                'ra': ra,
                'dec': dec,
                'radius': radius / 3600.0,  # Convert arcsec to degrees
                'download': True,
            }
            
            response = requests.get(
                self.ASASSN_API_URL,
                params=params,
                timeout=60
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                print(f"ASASSN query failed with status {response.status_code}")
                return None
                
        except requests.RequestException as e:
            print(f"ASASSN query error: {e}")
            return None


class ATLAS_Forced_Phot(PhotCatalog):
    """ATLAS Forced photometry server."""

    def query(
            self,
            ra: float,
            dec: float,
            radius: float = RADIUS_ARCSEC,
            days_ago: float = 200.,
            token: str = None
    ):
        f"""Query the ATLAS forced photometry service.

        {_QUERY_METHOD_DOCSTRING}
        """
        BASEURL = "https://fallingstar-data.com/forcedphot"

        if token is None:
            token = getattr(settings, 'ATLAS_API_KEY', None)
        if token is None:
            token = os.environ.get('ATLAS_API_KEY', None)

        if token is None:
            raise ValueError('No ATLAS API token provided. Set ATLAS_API_KEY in settings or environment.')

        headers = {'Authorization': f'Token {token}', 'Accept': 'application/json'}

        t_queryend = Time.now().mjd
        t_querystart = t_queryend - days_ago

        task_url = None
        while not task_url:
            with requests.Session() as s:
                resp = s.post(
                    f"{BASEURL}/queue/",
                    headers=headers,
                    data={
                        'ra': ra,
                        'dec': dec,
                        'send_email': False,
                        'mjd_min': t_querystart,
                        'mjd_max': t_queryend,
                        'use_reduced': False,
                    }
                )
                if resp.status_code == 201:
                    task_url = resp.json()['url']
                    print(f'The task URL is {task_url}')
                elif resp.status_code == 429:
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
                    return None

        result_url = None
        taskstarted_printed = False
        while not result_url:
            with requests.Session() as s:
                resp = s.get(task_url, headers=headers)

                if resp.status_code == 200:
                    if resp.json()['finishtimestamp']:
                        result_url = resp.json()['result_url']
                        print(f"Task is complete with results available at {result_url}")
                    elif resp.json()['starttimestamp']:
                        if not taskstarted_printed:
                            print(f"Task is running (started at {resp.json()['starttimestamp']})")
                            taskstarted_printed = True
                        time.sleep(2)
                    else:
                        time.sleep(4)
                else:
                    print(f'ERROR {resp.status_code}')
                    print(resp.text)
                    return None

        with requests.Session() as s:
            textdata = s.get(result_url, headers=headers).text

        atlas_phot = self._ATLAS_stack(textdata)
        return atlas_phot

    def _ATLAS_stack(self, textdata):
        """
        Stack ATLAS photometry data.
        
        Adapted from David Young's plot_atlas_fp.py
        https://github.com/thespacedoctor/plot-results-from-atlas-force-photometry-service
        """
        if not textdata or not textdata.strip():
            return []
            
        epochs = self._ATLAS_read_and_sigma_clip_data(textdata, clipping_sigma=2.2)

        magnitudes = {
            'c': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
            'o': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
            'I': {'mjds': [], 'mags': [], 'magErrs': [], 'lim5sig': []},
        }

        for epoch in epochs:
            if epoch["F"] in ["c", "o", "I"]:
                magnitudes[epoch["F"]]["mjds"].append(epoch["MJD"])
                magnitudes[epoch["F"]]["mags"].append(epoch["uJy"])
                magnitudes[epoch["F"]]["magErrs"].append(epoch["duJy"])
                magnitudes[epoch["F"]]['lim5sig'].append(epoch["mag5sig"])

        stacked_magnitudes = self._stack_photometry(magnitudes, binning_days=1)
        return stacked_magnitudes

    def _ATLAS_read_and_sigma_clip_data(self, filecontent, clipping_sigma=2.2):
        """
        Clean up data by performing sigma clipping.
        
        Adapted from David Young's plot_atlas_fp.py
        """
        if not filecontent:
            return []
            
        fp_data = filecontent.replace("###", "").replace(" ", ",").replace(
            ",,", ",").replace(",,", ",").replace(",,", ",").replace(",,", ",").splitlines()

        oepochs = []
        cepochs = []
        
        try:
            csv_reader = csv.DictReader(fp_data, dialect='excel', delimiter=',', quotechar='"')
            
            for row in csv_reader:
                for k, v in row.items():
                    try:
                        row[k] = float(v)
                    except (ValueError, TypeError):
                        pass
                
                try:
                    if row["duJy"] > 4000 or row["chi/N"] > 100 or row['mag5sig'] < 17.:
                        continue
                except (KeyError, TypeError):
                    continue
                    
                if row.get("F") == "c":
                    cepochs.append(row)
                elif row.get("F") == "o":
                    oepochs.append(row)
        except Exception as e:
            print(f"Error parsing ATLAS data: {e}")
            return []

        cepochs = sorted(cepochs, key=itemgetter('MJD'), reverse=False)
        oepochs = sorted(oepochs, key=itemgetter('MJD'), reverse=False)

        c_flux = [row["uJy"] for row in cepochs]
        o_flux = [row["uJy"] for row in oepochs]

        c_mask = _rolling_window_sigma_clip(c_flux, clipping_sigma=clipping_sigma, window_size=11)
        o_mask = _rolling_window_sigma_clip(o_flux, clipping_sigma=clipping_sigma, window_size=11)

        cepochs = [e for e, m in zip(cepochs, c_mask) if not m]
        oepochs = [e for e, m in zip(oepochs, o_mask) if not m]

        return cepochs + oepochs

    def _stack_photometry(self, magnitudes, binning_days=1.):
        """Stack photometry by binning observations."""
        summed_magnitudes = {
            'c': {'mjds': [], 'mags': [], 'magErrs': [], 'n': [], 'lim5sig': []},
            'o': {'mjds': [], 'mags': [], 'magErrs': [], 'n': [], 'lim5sig': []},
            'I': {'mjds': [], 'mags': [], 'magErrs': [], 'n': [], 'lim5sig': []},
        }

        all_data = []
        for fil, data in magnitudes.items():
            distinct_mjds = {}
            for mjd, flx, err, lim in zip(data["mjds"], data["mags"], data["magErrs"], data["lim5sig"]):
                key = str(int(math.floor(mjd / float(binning_days))))
                if key not in distinct_mjds:
                    distinct_mjds[key] = {
                        "mjds": [mjd],
                        "mags": [flx],
                        "magErrs": [err],
                        "lim5sig": [lim]
                    }
                else:
                    distinct_mjds[key]["mjds"].append(mjd)
                    distinct_mjds[key]["mags"].append(flx)
                    distinct_mjds[key]["magErrs"].append(err)
                    distinct_mjds[key]["lim5sig"].append(lim)

            for k, v in distinct_mjds.items():
                mean_mjd = sum(v["mjds"]) / len(v["mjds"])
                summed_magnitudes[fil]["mjds"].append(mean_mjd)
                
                mean_flux = sum(v["mags"]) / len(v["mags"])
                summed_magnitudes[fil]["mags"].append(mean_flux)
                
                sum_of_squares = sum(x ** 2 for x in v["magErrs"])
                comb_error = math.sqrt(sum_of_squares) / len(v["magErrs"])
                summed_magnitudes[fil]["magErrs"].append(comb_error)
                
                comb_5sig_limit = 23.9 - 2.5 * math.log10(5. * comb_error) if comb_error > 0 else 99.0
                summed_magnitudes[fil]["lim5sig"].append(comb_5sig_limit)
                
                n = len(v["mjds"])
                summed_magnitudes[fil]["n"].append(n)
                
                all_data.append({
                    'mjd': mean_mjd,
                    'uJy': mean_flux,
                    'duJy': comb_error,
                    'F': fil,
                    'n': n,
                    'mag5sig': comb_5sig_limit
                })

        return all_data


class ZTF_Forced_Phot(PhotCatalog):
    """ZTF Forced photometry server (not yet implemented)."""

    def query(
            self,
            ra: float,
            dec: float,
            radius: float = RADIUS_ARCSEC
    ):
        f"""Query the ZTF forced photometry service.

        {_QUERY_METHOD_DOCSTRING}
        """
        raise NotImplementedError("ZTF forced photometry query not yet implemented")
