from django.conf import settings
from importlib import import_module

from tom_dataproducts.models import ReducedDatum


DEFAULT_DATA_PROCESSOR_CLASS = 'tom_dataproducts.data_processor.DataProcessor'


def run_data_processor(dp):
    """
    Reads the `data_product_type` from the dp parameter and imports the corresponding `DataProcessor` specified in
    `settings.py`, then runs `process_data` and inserts the returned values into the database.

    :param dp: DataProduct which will be processed into a list
    :type dp: DataProduct

    :returns: QuerySet of `ReducedDatum` objects created by the `run_data_processor` call
    :rtype: `QuerySet` of `ReducedDatum`
    """

    try:
        processor_class = settings.DATA_PROCESSORS[dp.data_product_type]
    except Exception:
        processor_class = DEFAULT_DATA_PROCESSOR_CLASS

    try:
        mod_name, class_name = processor_class.rsplit('.', 1)
        mod = import_module(mod_name)
        clazz = getattr(mod, class_name)
    except (ImportError, AttributeError):
        raise ImportError('Could not import {}. Did you provide the correct path?'.format(processor_class))

    data_processor = clazz()
    data = data_processor.process_data(dp)

    reduced_datums = [ReducedDatum(target=dp.target, data_product=dp, data_type=dp.data_product_type,
                                   timestamp=datum[0], value=datum[1], source_name=datum[2]) for datum in data]
    ReducedDatum.objects.bulk_create(reduced_datums)

    return ReducedDatum.objects.filter(data_product=dp)
