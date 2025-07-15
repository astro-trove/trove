CREATE MATERIALIZED VIEW galaxy_catalog_view AS

-- Milliquas quasar catalog
SELECT
	'milliquas_q3c'::text AS source_table,
	mid::bigint AS source_id,
	ra,
	dec,
	name::text
FROM milliquas_q3c

UNION ALL

-- DESI Spec catalog
SELECT
	'desi_spec_q3c'::text AS source_table,
	did::bigint AS source_id,
	target_ra AS ra,
	target_dec AS dec,
	targetid::text AS name
FROM desi_spec_q3c

UNION ALL

-- Glade galaxy catalog
SELECT
	'glade_plus_q3c'::text AS source_table,
	gid::bigint AS source_id,
	ra,
	dec,
	gn::text AS name
FROM glade_plus_q3c

UNION ALL

-- GWGC galaxy catalog
SELECT
	'gwgc_q3c'::text AS source_table,
	gid::bigint AS source_id,
	ra,
	dec,
	name::text
FROM gwgc_q3c

UNION ALL

-- Hecate galaxt catalog
SELECT
	'hecate_q3c'::text AS source_table,
	hid::bigint AS source_id,
	ra,
	dec,
	objname::text AS name
FROM hecate_q3c

UNION ALL

-- The galaxies from the Legacy Survey DR10
-- Anything r < 18 mag and mtype==PSF is a point source and shouldn't be included
-- 18 mag is 63.09573444801933 nanomaggy (the units used in this table)
-- ALSO note that nanomaggy's are linear and increase with increased brightness
-- unlike magnitudes, so the logic is a bit inverted here
SELECT
	'ls_dr10_q3c'::text AS source_table,
	lid::bigint AS source_id,
	ra,
	declination,
	objid::text AS name
FROM ls_dr10_q3c
WHERE NOT (flux_r > 63.09573444801933 AND mtype = 'PSF')

UNION ALL

-- Panstarrs 1 galaxies
-- We filter out anything with a ps_score <= 0.83
-- From https://iopscience.iop.org/article/10.1088/1538-3873/aae3d9/pdf
SELECT
	'ps1_q3c'::text AS source_table,
	pid::bigint AS source_id,
	ra,
	dec,
	objid::text AS name
FROM ps1_q3c
WHERE ps_score <= 0.83

UNION ALL

-- SDSS 12 photo z catalog
SELECT
	'sdss12photoz_q3c'::text AS source_table,
	sid::bigint AS source_id,
	ra,
	dec,
	sdssid::text AS name
FROM sdss12photoz_q3c;
