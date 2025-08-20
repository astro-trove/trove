import astropy

def processing(table: astropy.table.table.Table) -> astropy.table.table.Table:
    # TODO: separate per-table and per-chunk processing
    # #####################################################################
    # Operations such as unit conversions change of column namesto 
    # perform against the the table before inserting into DB.
    # #####################################################################
    
    # ex: t.keep_columns(['ra', 'dec']) # modifies in place
    # ex: t['ra'] *= 3.141592653589793 / 180 # just an ex, idk if anyone will use rads
    # ex: t['ra'].name = 'ra_rads'
    # ex: t['redshift'].name = 'red_shift'

    return table

# extra_SQL = []

