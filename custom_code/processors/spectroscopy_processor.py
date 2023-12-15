import mimetypes
from specutils import Spectrum1D
from tom_dataproducts.processors.data_serializers import SpectrumSerializer
from tom_dataproducts.processors.spectroscopy_processor import SpectroscopyProcessor as OldSpectroscopyProcessor
from tom_dataproducts.exceptions import InvalidFileFormatException
from lightcurve_fitting.speccal import readspec
import numpy as np


class SpectroscopyProcessor(OldSpectroscopyProcessor):

    def process_data(self, data_product):
        """
        Routes a spectroscopy processing call to a method specific to a file-format, then serializes the returned data.

        Parsing of the spectrum file is handled by the lightcurve-fitting
        package: https://griffin-h.github.io/lightcurve_fitting/api.html#lightcurve_fitting.speccal.readspec.

        Serialization removes any NaN values, which cannot be stored in a JSONField.

        :param data_product: Spectroscopic DataProduct which will be processed into the specified format for database
        ingestion
        :type data_product: DataProduct

        :returns: python list of 2-tuples, each with a timestamp and corresponding data
        :rtype: list
        """
        wavelength, flux, obs_date, telescope, instrument = readspec(data_product.data.path)
        if len(flux) < 1:
            raise InvalidFileFormatException('Empty table or invalid file type')
        spectrum = Spectrum1D(flux=flux * self.DEFAULT_FLUX_CONSTANT,
                              spectral_axis=wavelength * self.DEFAULT_WAVELENGTH_UNITS)
        serialized_spectrum = FiniteSpectrumSerializer().serialize(spectrum)

        return [(obs_date.to_datetime(), serialized_spectrum, '')]  # no support for source_name yet


class FiniteSpectrumSerializer(SpectrumSerializer):

    def serialize(self, spectrum: Spectrum1D) -> dict:
        """
        Serializes a Spectrum1D in order to store in a ReducedDatum object. The serialization stores only what's
        necessary to rebuild the Spectrum1D--namely, flux and wavelength, and their respective units.

        :param spectrum: Spectrum1D to be serialized
        :type spectrum: specutils.Spectrum1D

        :returns: JSON representation of spectrum
        :rtype: dict
        """
        good = np.isfinite(spectrum.flux)
        return {
            'flux': spectrum.flux.value[good].tolist(),
            'flux_units': spectrum.flux.unit.to_string(),
            'wavelength': spectrum.wavelength.value[good].tolist(),
            'wavelength_units': spectrum.wavelength.unit.to_string(),
        }
