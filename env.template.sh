#!/bin/sh


# +
# $1 = source code
# $2 = test flag
# -
export SAGUARO_TOM_CODE=${1:-'/var/www/saguaro_tom'}
export SAGUARO_TOM_FLAG=${2:-''}

echo "SAGUARO_TOM_CODE = ${SAGUARO_TOM_CODE}"
echo "SAGUARO_TOM_FLAG = ${SAGUARO_TOM_FLAG}"


# +
# edit as you see fit
# -
export GEM_N_API_KEY=''
export GEM_S_API_KEY=''
export LCO_API_KEY=''
export MMT_API_KEY=''
export POSTGRES_DB=''
export POSTGRES_HOST=''
export POSTGRES_PASSWORD=''
export POSTGRES_PORT=''
export POSTGRES_USER=''
export SECRET_KEY=''
export TNS_API_KEY=''


# +
# flag
# -
[[ ! -z ${SAGUARO_TOM_FLAG} ]] && PGPASSWORD=${POSTGRES_PASSWORD} psql -h ${POSTGRES_HOST} -p ${POSTGRES_PORT} -d ${POSTGRES_DB} -U  ${POSTGRES_USER} -P pager=off -c "\d" || echo "Database not available!"


# +
# credential(s)
# -
if [[ ! -f ${SAGUARO_TOM_CODE}/settings_local.py ]]; then
  echo "#!/usr/bin/env python3"                     >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "GEM_N_API_KEY = '${GEM_N_API_KEY}'"         >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "GEM_S_API_KEY = '${GEM_S_API_KEY}'"         >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "LCO_API_KEY = '${LCO_API_KEY}'"             >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "MMT_API_KEY = '${MMT_API_KEY}'"             >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "POSTGRES_DB = '${POSTGRES_DB}'"             >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "POSTGRES_HOST = '${POSTGRES_HOST}'"         >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "POSTGRES_PASSWORD = '${POSTGRES_PASSWORD}'" >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "POSTGRES_PORT = ${POSTGRES_PORT}"           >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "POSTGRES_USER = '${POSTGRES_USER}'"         >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "SECRET_KEY = '${SECRET_KEY}'"               >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  echo "TNS_API_KEY = '${TNS_API_KEY}'"             >> ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
  chown www-data:www-data ${SAGUARO_TOM_CODE}/saguaro_tom/settings_local.py
fi
