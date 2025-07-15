#!/usr/bin/env python3


# +
# import(s)
# -
import os
import sys

from django.core.wsgi import get_wsgi_application


# +
# message
# -
print(f'SAGUARO-TOM> Python Version: {sys.version}')
print(f'SAGUARO-TOM> Python Info: {sys.version_info}')


# +
# setup / path(s)
# -
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'saguaro_tom.settings')


# +
# start
# -
application = get_wsgi_application()
