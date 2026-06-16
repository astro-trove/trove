# Test fixtures for db-static and local NLE ingestion

## GW170817 (issue #5)

Manual ingestion of a single non-localized event requires **both**:

1. **`GW170817-update.json`** — IGWN-style alert metadata (no skymap bytes; JSON cannot carry raw FITS).
2. **`GW170817-bayestar.fits.gz`** — BAYESTAR skymap from GraceDB / LIGO public data for event `G298107` (GW170817).

The FITS header `OBJECT` is `G298107`; the alert fixture uses superevent id **`GW170817`** for Trove.

**Format notes:** GraceDB / `create_localization_for_skymap` expect **multi-order** FITS (`UNIQ`, `PROBDENSITY`). The bundled `GW170817-bayestar.fits.gz` is classic BAYESTAR (`PROB`, NESTED). `upload_local_nle` gunzips `.gz` files and converts classic maps to multi-order automatically (same as using `bayestar.multiorder.fits` from GraceDB).

### Ingest from the repository root

```bash
python manage.py shell -c "
from custom_code.nle_ingestion import upload_local_nle
upload_local_nle('tests/data/GW170817-update.json', 'tests/data/GW170817-bayestar.fits.gz')
"
```

Or in pytest (see `tests/test_nle_ingestion.py`).

### Source

- Skymap: `bayestar.fits.gz` (classic NESTED BAYESTAR; converted to multi-order UNIQ/PROBDENSITY on ingest).
- Alert JSON: synthetic fixture matching the [IGWN public alert schema](https://rtd.igwn.org/projects/userguide/en/latest/content.html).

## db-static Docker image

Large snapshot tarballs (e.g. `db-static-20251222.tar`) are gitignored; place them here locally when running `tests/test_db_static.sh`.
