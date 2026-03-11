"""
Spectrum file reader - replacement for lightcurve_fitting.speccal.readspec.

This module provides vectorized, performant spectrum reading without the
lightcurve-fitting dependency. Uses only astropy and standard library.
"""
import json
import os
import re
import warnings
from typing import Optional, Tuple

import numpy as np
from astropy import units as u
from astropy.io import ascii, fits
from astropy.time import Time
from astropy.wcs import WCS


def _remove_bad_cards(hdr: fits.Header) -> fits.Header:
    """Remove problematic entries from a FITS header."""
    bad_keys = []
    for card in hdr.cards:
        try:
            card.verify('fix')
        except Exception:
            bad_keys.append(card.keyword)
    for key in bad_keys:
        try:
            hdr.remove(key)
        except KeyError:
            pass
    return hdr


def _remove_duplicate_wcs(hdr: fits.Header, keep_number: int = 0) -> None:
    """Remove duplicate WCS header keywords, keeping the specified occurrence."""
    wcs_keys = ['CTYPE1', 'CTYPE2', 'CRPIX1', 'CRPIX2', 'CRVAL1', 'CRVAL2',
                'CD1_1', 'CD2_2', 'CD1_2', 'CD2_1']
    for key in wcs_keys:
        if key in hdr and hdr.count(key) > 1:
            card = hdr.cards[(key, keep_number)]
            hdr.remove(card.keyword, remove_all=True)
            hdr[card.keyword] = (card.value, card.comment)


def _read_fits_spectrum(filename: str) -> Tuple[np.ndarray, np.ndarray, fits.Header]:
    """
    Read a spectrum from a FITS file.
    
    Returns wavelength array, flux array, and header.
    """
    with fits.open(filename) as hdulist:
        hdu = None
        for h in hdulist:
            if h.header.get('extname') == 'SCI':
                hdu = h
                break
        if hdu is None:
            for h in hdulist:
                if h.data is not None:
                    hdu = h
                    break
        if hdu is None:
            raise ValueError('No extensions have any data')
        
        data = hdu.data
        hdr = hdu.header.copy()
        
        if isinstance(hdu, fits.BinTableHDU):
            wl = np.asarray(data['wavelength'], dtype=np.float64)
            flux = np.asarray(data['flux'], dtype=np.float64)
        else:
            data = np.moveaxis(data, np.arange(data.ndim), np.argsort(data.shape))
            flux = data.flatten()[:max(data.shape)].astype(np.float64)
            _remove_duplicate_wcs(hdr)
            
            cunit = hdr.get('CUNIT1', '')
            if cunit.lower() in ['angstroms', 'deg', 'pixel']:
                hdr['CUNIT1'] = 'Angstrom'
            
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                wcs = WCS(_remove_bad_cards(hdr), naxis=1, relax=False, fix=False)
            
            pixel_indices = np.arange(len(flux))
            if 'CUNIT1' in hdr:
                wl = wcs.pixel_to_world(pixel_indices).to(hdr['CUNIT1']).value
            else:
                wl = wcs.wcs_pix2world(pixel_indices, 0)[0]
    
    return wl, flux, hdr


def _read_ascii_spectrum(filename: str) -> Tuple[np.ndarray, np.ndarray, dict]:
    """
    Read a spectrum from an ASCII file.
    
    Returns wavelength array, flux array, and metadata dict.
    """
    t = ascii.read(filename, format='basic')
    wl = np.asarray(t.columns[0], dtype=np.float64)
    flux = np.asarray(t.columns[1], dtype=np.float64)
    
    hdr = {}
    comments = t.meta.get('comments', [])
    for line in comments:
        match = re.search(r'([^ ]*)\s*[=:]\s*([^/]*)', line)
        if match:
            kwd, val = match.groups()
            hdr[kwd.strip(' #')] = val.strip(' "\'')
    
    return wl, flux, hdr


def _read_json_spectrum(filename: str) -> Tuple[np.ndarray, np.ndarray, dict]:
    """
    Read spectra from a JSON file in Open Astronomy Catalog schema.
    
    Returns wavelength array, flux array, and metadata dict for the first spectrum.
    """
    with open(filename) as f:
        json_dict = json.load(f)
    
    base_name = os.path.splitext(os.path.basename(filename))[0]
    rows = json_dict.get(base_name, json_dict)
    
    if isinstance(rows, dict) and 'spectra' in rows:
        rows = rows['spectra']
    
    if not rows:
        return np.array([]), np.array([]), {}
    
    first_spec = rows[0] if isinstance(rows, list) else rows
    data = first_spec.get('data', [])
    
    data_arr = np.array(data, dtype=np.float64)
    if data_arr.size == 0:
        return np.array([]), np.array([]), {}
    
    wl = data_arr[:, 0] * 0.1  # Convert to Angstroms (OSC uses nm * 10)
    flux = data_arr[:, 1]
    
    hdr = {
        'time': first_spec.get('time', ''),
        'u_time': first_spec.get('u_time', 'MJD'),
        'telescope': first_spec.get('telescope', ''),
        'instrument': first_spec.get('instrument', ''),
    }
    
    return wl, flux, hdr


def _convert_spectrum_units(
    wl: np.ndarray,
    flux: np.ndarray,
    hdr: dict,
    default_bunit: str = 'erg / (Angstrom cm2 s)',
    default_cunit: str = 'Angstrom'
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Convert spectrum to standard units using header info.
    
    Vectorized unit conversion for performance.
    """
    bunit = hdr.get('BUNIT', default_bunit)
    if bunit == 'adu':
        bunit = default_bunit
    bunit = bunit.replace('Ang', 'Angstrom').replace(' A ', ' Angstrom ')
    if 'Angstrom' not in bunit and bunit.endswith(' A'):
        bunit = bunit[:-2] + ' Angstrom'
    
    cunit = hdr.get('CUNIT1', hdr.get('XUNITS', default_cunit))
    if cunit.lower() == 'angstroms':
        cunit = cunit.rstrip('s')
    
    try:
        wl_q = u.Quantity(wl, cunit).to(default_cunit)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            flux_q = u.Quantity(flux, bunit).to(
                default_bunit, u.equivalencies.spectral_density(wl_q)
            )
        return wl_q.value, flux_q.value
    except Exception:
        return wl, flux


def _parse_date_from_header(hdr: dict) -> Optional[Time]:
    """
    Parse observation date from header keywords.
    
    Tries multiple common keywords in priority order.
    """
    date_keywords = [
        'MJD-OBS', 'MJD_OBS', 'MJD', 'JD', 'DATE-AVG', 'UTMIDDLE',
        'DATE-OBS', 'DATE_BEG', 'UTSHUT', 'OBS_DATE', 'AVE_MJD'
    ]
    
    for kwd in date_keywords:
        val = hdr.get(kwd)
        if not val:
            continue
        
        try:
            if 'MJD' in kwd:
                return Time(float(val), format='mjd')
            elif 'JD' in kwd:
                jd_val = float(val)
                if jd_val > 2400000:
                    return Time(jd_val, format='jd')
                else:
                    return Time(jd_val + 2400000, format='jd')
            elif 'T' in str(val):
                return Time(val)
            elif kwd == 'OBS_DATE':
                return Time(str(val).split('+')[0])
            elif '-' in str(val):
                for kwd2 in ['UTMIDDLE', 'EXPSTART', 'UT']:
                    ut_val = hdr.get(kwd2)
                    if ut_val and isinstance(ut_val, str) and ':' in ut_val:
                        return Time(f"{val}T{ut_val}")
                    elif ut_val:
                        try:
                            ut_float = float(ut_val)
                            h = int(ut_float)
                            m = int((ut_float * 60) % 60)
                            s = int((ut_float * 3600) % 60)
                            return Time(f"{val}T{h:02d}:{m:02d}:{s:02d}")
                        except (ValueError, TypeError):
                            pass
                return Time(val)
        except Exception:
            continue
    
    return None


def _parse_date_from_filename(filename: str) -> Optional[Time]:
    """
    Parse observation date from filename patterns.
    
    Handles JD, MJD, ISO dates, and TNS-style dates.
    """
    patterns = [
        (r'24[0-9]{5}\.[0-9]+', 'jd'),
        (r'(19|20)[0-9]{2}-(0[0-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])_'
         r'([01][0-9]|2[0-4])-[0-5][0-9]-[0-5][0-9]', 'tns'),
        (r'([12][90][0-9]{2})-?(0[0-9]|1[0-2])-?(0[1-9]|[12][0-9]|3[01])'
         r'(\.[0-9]+)?', 'iso'),
        (r'[0-9]{3}d', 'phase'),
        (r'[0-9]{5}(\.[0-9]+)?', 'mjd'),
    ]
    
    for pattern, fmt in patterns:
        match = re.search(pattern, filename)
        if match:
            try:
                m = match.group()
                if fmt == 'jd':
                    return Time(float(m), format='jd')
                elif fmt == 'tns':
                    d, t = m.split('_')
                    return Time(f"{d}T{t.replace('-', ':')}")
                elif fmt == 'iso':
                    groups = match.groups()
                    date = Time('-'.join(g for g in groups[:3] if g))
                    if groups[3]:
                        date += float(groups[3]) * u.day
                    return date
                elif fmt == 'phase':
                    return Time(float(m[:-1]), format='mjd')
                elif fmt == 'mjd':
                    return Time(float(m), format='mjd')
            except Exception:
                continue
    
    return None


def readspec(
    filename: str,
    verbose: bool = False,
    return_header: bool = False
) -> Tuple[np.ndarray, np.ndarray, Optional[Time], str, str, ...]:
    """
    Read a spectrum from a FITS, ASCII, or JSON file.
    
    Identifies observation date, telescope, and instrument from headers
    or filename patterns. Converts to standard units.
    
    Parameters
    ----------
    filename : str
        Path to the spectrum file
    verbose : bool, optional
        Print date and filename if True
    return_header : bool, optional
        Return the header/metadata as an additional return value
    
    Returns
    -------
    wavelength : np.ndarray
        Wavelengths in Angstroms
    flux : np.ndarray
        Fluxes in erg/(s cm² Å)
    date : astropy.time.Time or None
        Observation time if identifiable
    telescope : str
        Telescope name if identifiable
    instrument : str
        Instrument name if identifiable
    header : dict (only if return_header=True)
        Full header/metadata
    """
    ext = os.path.splitext(filename)[1].lower()
    
    if ext == '.fits':
        wl, flux, hdr = _read_fits_spectrum(filename)
        hdr = dict(hdr)
    elif ext == '.json':
        wl, flux, hdr = _read_json_spectrum(filename)
    else:
        wl, flux, hdr = _read_ascii_spectrum(filename)
    
    date = _parse_date_from_header(hdr)
    if date is None:
        date = _parse_date_from_filename(filename)
    
    telescope = ''
    for key in ['TELESCOP', 'TELESCOPE', 'OBSERVAT']:
        if key in hdr:
            telescope = str(hdr[key]).strip()
            break
    
    instrument = ''
    for key in ['INSTRUME', 'INSTRUMENT', 'INSTR', 'INSTRUMENT_ID']:
        if key in hdr:
            instrument = str(hdr[key]).strip()
            break
    
    wl, flux = _convert_spectrum_units(wl, flux, hdr)
    
    if verbose and date:
        print(date.isot, filename)
    
    if return_header:
        return wl, flux, date, telescope, instrument, hdr
    else:
        return wl, flux, date, telescope, instrument
