"""
Some useful variables that will be used throughout this entire directory
"""

RADIUS_ARCSEC = 2.0

_QUERY_METHOD_DOCSTRING = f"""
Parameters
----------
ra : float
    The RA in degrees
dec : float
    The declination in degrees
radius : float, default={RADIUS_ARCSEC}
    The search radius in arcseconds

Returns
-------
QueryResult
    The result of the query, either a Galaxy or PointSource class
"""

# use 0.83 as the default threshold
# this is from 
PS1_POINT_SOURCE_THRESHOLD = 0.83
