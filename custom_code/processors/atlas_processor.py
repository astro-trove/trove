from astropy import units
from astropy.time import Time, TimezoneInfo
import numpy as np
import logging

from tom_dataproducts.exceptions import InvalidFileFormatException
from tom_dataproducts.processors.atlas_processor import AtlasProcessor
from kne_cand_vetting.survey_phot import ATLAS_stack

logger = logging.getLogger(__name__)


class ClippedStackedAtlasProcessor(AtlasProcessor):
    """
    Applies the sigma clipping and stacking recommended by David Young:
    https://gist.github.com/thespacedoctor/86777fa5a9567b7939e8d84fd8cf6a76#file-plot_atlas_fp-py
    """

    def _process_photometry_from_plaintext(self, data_product):
        """
        Processes the photometric data from a plaintext file into a list of dicts. File is read using astropy as
        specified in the below documentation. The file is expected to be a multi-column delimited space delimited
        text file, as produced by the ATLAS forced photometry service at https://fallingstar-data.com/forcedphot
        See https://fallingstar-data.com/forcedphot/resultdesc/ for a description of the output format.

        The header looks like this:
        ###MJD   m   dm  uJy   duJy F err chi/N   RA  Dec   x   y  maj  min   phi  apfit mag5sig Sky   Obs

        :param data_product: ATLAS Photometric DataProduct which will be processed into a list of dicts
        :type data_product: DataProduct

        :returns: python list containing the photometric data from the DataProduct
        :rtype: list
        """
        photometry = []
        signal_to_noise_cutoff = 3.0  # cutoff to turn magnitudes into non-detection limits

        with open(data_product.data.path) as f:
            filecontent = f.read()

        data = ATLAS_stack(filecontent, logger)
        if len(data) < 1:
            raise InvalidFileFormatException('Empty table or invalid file type')

        try:
            for datum in data:
                time = Time(datum['mjd'], format='mjd')
                utc = TimezoneInfo(utc_offset=0*units.hour)
                time.format = 'datetime'
                value = {
                    'timestamp': time.to_datetime(timezone=utc),
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

                photometry.append(value)
        except Exception as e:
            raise InvalidFileFormatException(e)

        return photometry
