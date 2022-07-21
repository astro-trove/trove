from specutils import Spectrum1D
from tom_dataproducts.processors.spectroscopy_processor import SpectroscopyProcessor as OldSpectroscopyProcessor
from tom_dataproducts.exceptions import InvalidFileFormatException
from lightcurve_fitting.speccal import readspec


class SpectroscopyProcessor(OldSpectroscopyProcessor):

    def _process_spectrum_from_plaintext(self, data_product):
        """
        Processes the data from a spectrum from a plaintext file into a Spectrum1D object, which can then be serialized
        and stored as a ReducedDatum for further processing or display. Text files are read using the lightcurve-fitting
        package: https://griffin-h.github.io/lightcurve_fitting/api.html#lightcurve_fitting.speccal.readspec.

        Parameters
        ----------
        :param data_product: Spectroscopic DataProduct which will be processed into a Spectrum1D
        :type data_product: tom_dataproducts.models.DataProduct

        :returns: Spectrum1D object containing the data from the DataProduct
        :rtype: specutils.Spectrum1D

        :returns: Datetime of observation, if it is in the comments and the file is from a supported facility, current
            datetime otherwise
        :rtype: datetime.datetime
        """

        wavelength, flux, date_obs, telescope, instrument = readspec(data_product.data.path)
        if len(flux) < 1:
            raise InvalidFileFormatException('Empty table or invalid file type')
        spectrum = Spectrum1D(flux=flux * self.DEFAULT_FLUX_CONSTANT,
                              spectral_axis=wavelength * self.DEFAULT_WAVELENGTH_UNITS)
        return spectrum, date_obs.to_datetime()
