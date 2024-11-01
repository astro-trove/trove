from tom_observations.facilities.lco import LCOFacility
from tom_dataproducts.data_processor import run_data_processor, DataProcessor
from tom_dataproducts.models import DataProduct
from tom_dataproducts.utils import create_image_dataproduct
from django.core.files.base import ContentFile
from django.conf import settings
import requests
import mimetypes
import tarfile
import os
import logging

logger = logging.getLogger(__name__)


class CustomLCOFacility(LCOFacility):
    def save_data_products(self, observation_record, product_id=None):
        final_products = []
        products = self.data_products(observation_record.observation_id, product_id)

        for product in products:
            dp, created = DataProduct.objects.get_or_create(
                product_id=product['id'],
                target=observation_record.target,
                observation_record=observation_record,
                data_product_type='LCO',  # same as the built-in method except for this line
            )
            if created:
                product_data = requests.get(product['url']).content
                dfile = ContentFile(product_data)
                dp.data.save(product['filename'], dfile)
                dp.save()
                logger.info('Saved new dataproduct: {}'.format(dp.data))
                run_data_processor(dp)
            if settings.AUTO_THUMBNAILS:
                create_image_dataproduct(dp)
                dp.get_preview()
            final_products.append(dp)
        return final_products


class LCODataProcessor(DataProcessor):
    def process_data(self, data_product):
        mimetype = mimetypes.guess_type(data_product.data.path)[-1]
        if mimetype == 'gzip':
            logger.info('Untarring FLOYDS file: {}'.format(data_product.data))
            with tarfile.open(fileobj=data_product.data) as targz:
                for member in targz.getmembers():
                    if member.name.endswith('_2df_ex.fits'):
                        fitsfile = targz.extractfile(member)
                        fitsname = os.path.basename(member.name)
                        dp, created = DataProduct.objects.get_or_create(
                            product_id=member.name,
                            target=data_product.target,
                            observation_record=data_product.observation_record,
                            data=ContentFile(fitsfile.read(), fitsname),
                            data_product_type='spectroscopy',
                        )
                        logger.info('Saved new dataproduct: {}'.format(dp.data))
                        run_data_processor(dp)
        return []
