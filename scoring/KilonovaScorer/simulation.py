import logging
import time
import multiprocessing
from multiprocessing import Pool, cpu_count
import numpy as np
from tqdm import tqdm
from astropy.cosmology import Planck18 as cosmo
from astropy.cosmology import z_at_value
from astropy import units as u
import redback
from redback.model_library import all_models_dict
from bilby.core.prior import Uniform

import warnings
warnings.filterwarnings("ignore", "Wswiglal-redir-stdio")
import lal

logger = logging.getLogger(__name__)

#: redback's own t_max for kilonova SED grids, in source-frame seconds (81 d).
#: Seven of redback 1.18's ten kilonova models pass this; `get_optimal_time_array`
#: documents it as the kilonova example.
_REDBACK_KN_TMAX_S = 7e6

#: The tighter cap that `two_component_kilonova_model` and
#: `one_component_kilonova_model` pass instead: 6 days, source frame.
_REDBACK_SHORT_TMAX_S = 86400 * 6


def patch_redback_time_cap():
    """Raise redback's 6-day SED cap on the two-component kilonova models.

    WHY. ``two_component_kilonova_model`` builds its spectral time series on

        get_optimal_time_array(1e-2, 86400*6, ...)

    so the SED grid stops at 6 days in the SOURCE frame however far out you
    ask for times. ``get_optimal_time_array`` clamps to that maximum --
    ``eval_max = min(t_max, user_max * 2.0)``, and the array ends at
    ``t_max`` -- and the resulting ``RedbackTimeSeriesSource`` returns its
    edge value for any phase past the end of its domain. The magnitude is
    therefore EXACTLY constant beyond ``6 * (1 + z)`` days observer frame:
    at z = 0.056 every light curve in a 0-10 d grid freezes at 6.34 d and
    36% of the time axis is a flat line.

    That is not physics -- kilonovae keep fading -- and it is not consistent
    within redback either: seven of its ten kilonova models pass 7e6 s (81 d)
    for the same argument, and the utility's docstring gives 7e6 as the
    kilonova example. Two models were left on a much tighter cap.

    Measured cost of leaving it in place, paired on identical parameters:
    zero difference through 6 d (so this changes nothing that was already
    right), then the capped population runs too BRIGHT by a median 1.16 mag
    at 10 d, up to 7.16 mag. A frozen, over-bright reference pushes a
    genuinely fading kilonova into the faint tail and depresses P_tail --
    a late-time bias on top of the selection effect in IMPROVEMENTS #19.

    Narrow by construction: it rewrites one argument of one function, only
    when that argument is the 6-day value, and leaves every other caller of
    ``get_optimal_time_array`` untouched. Idempotent, so workers may call it
    freely. Remove once redback ships the wider cap upstream.
    """
    from redback.transient_models import kilonova_models as _km

    if getattr(_km.get_optimal_time_array, "_trove_tmax_patched", False):
        return

    _original = _km.get_optimal_time_array

    def _widened(t_min, t_max, resolution, user_times=None, time_units="seconds"):
        if abs(t_max - _REDBACK_SHORT_TMAX_S) < 1.0:
            t_max = _REDBACK_KN_TMAX_S
        return _original(t_min, t_max, resolution,
                         user_times=user_times, time_units=time_units)

    _widened._trove_tmax_patched = True
    _widened._trove_original = _original
    _km.get_optimal_time_array = _widened


def _prewarm_bandpasses(bands):
    """Download and cache every bandpass once, serially, in this process.

    Guards against the concurrent-download race described in
    :func:`simulate_kilonova`. Safe to call repeatedly: a cached band is a
    no-op. A band that cannot be resolved is reported and left alone rather
    than raised on -- the simulation itself will fail on it with a clearer
    message than this helper can give.
    """
    try:
        import sncosmo
    except ImportError:  # redback can be present without sncosmo exposed
        return

    missing = []
    for band in bands:
        try:
            sncosmo.get_bandpass(band)
        except Exception as exc:  # noqa: BLE001 - report, do not abort
            missing.append(f"{band} ({type(exc).__name__})")
    if missing:
        print(f"⚠ bandpasses that would not load: {', '.join(missing)}", flush=True)
    else:
        print(f"✔ {len(bands)} bandpasses cached", flush=True)


# Worker function must be top-level for multiprocessing
def simulate_single_sample(sample_id, MODEL_NAME, TIME, FILTER_BANDS, z, mu, RANDOM_SEED=42):
    import numpy as np
    import redback
    from bilby.core.prior import Uniform
    from redback.model_library import all_models_dict

    # Applied in the parent too, but repeated here so the grid is correct under
    # every multiprocessing start method: 'fork' inherits the parent's patched
    # module, 'spawn' and 'forkserver' import redback fresh and would not.
    patch_redback_time_cap()

    # Seed per sample, deterministically. Two consequences:
    #  * grids become reproducible (IMPROVEMENTS #11 -- previously each worker
    #    drew from an unseeded global RNG, so a grid could never be regenerated);
    #  * grids at different distances become PAIRED -- sample_id i draws the same
    #    physical parameters everywhere, so comparing two distances isolates the
    #    redshift effect instead of measuring sampling noise between independent
    #    draws. Without this a distance-sensitivity test is dominated by a KS
    #    floor of ~sqrt(2/N).
    #
    # Both generators must be seeded. bilby >= 2.0 draws priors from its OWN
    # Generator (`bilby.core.utils.random.rng`), not the legacy `np.random`
    # global -- `Prior.sample()` calls `random.rng.uniform(0, 1)`. Seeding only
    # np.random therefore did nothing to the parameter draws, which is how the
    # first ladder came out neither reproducible nor paired despite the seed
    # call being present. np.random is still seeded because redback uses it for
    # anything downstream of the prior.
    import bilby.core.utils.random as _bilby_random

    _bilby_random.seed(RANDOM_SEED + sample_id)
    np.random.seed(RANDOM_SEED + sample_id)

    prior = redback.priors.get_priors(model=MODEL_NAME)
    if MODEL_NAME == 'two_component_kilonova_model':
        prior['mej_1'] = Uniform(minimum=1e-4, maximum=0.1,  name='mej_1', latex_label='$M_{\\mathrm{ej}~1}~(M_\\odot)$', unit=None,  boundary=None)
        prior['mej_2'] = Uniform(minimum=1e-4, maximum=0.1,  name='mej_2', latex_label='$M_{\\mathrm{ej}~2}~(M_\\odot)$', unit=None, boundary=None)
        #Ejecta velocity:
        prior['vej_1'] = Uniform(minimum=0.01, maximum=0.7, name='vej_1', latex_label='$v_{\\mathrm{ej}~1}~(c)$', unit=None, boundary=None)
        prior['vej_2'] = Uniform(minimum=0.01, maximum=0.7, name='vej_2', latex_label='$v_{\\mathrm{ej}~1}~(c)$', unit=None, boundary=None)
        #Kappa- opacity Blue + Red:
        prior['kappa_1'] = Uniform(minimum=0.1, maximum=0.5,name='kappa_1', latex_label='$\\kappa_{1}~(\\mathrm{cm}^{2}/\\mathrm{g})$', unit=None, boundary=None)
        prior['kappa_2'] = Uniform(minimum=1, maximum=30,name='kappa_2', latex_label='$\\kappa_{2}~(\\mathrm{cm}^{2}/\\mathrm{g})$', unit=None, boundary=None)
    params = prior.sample()
    params['redshift'] = z

    # Arrays, not a DataFrame. The grid store holds one lightcurve per row with
    # the epoch encoded by position, so the (sample, band, time, magnitude,
    # parameters...) long form the Parquet writer needed is pure overhead here:
    # it inflated every sample to len(TIME) * len(bands) rows carrying the same
    # 10 parameter values over and over, and was the reason a rung measured
    # 25 GB in a naive table. float32 because that is the store's dtype -- doing
    # it here means one narrowing rather than one per chunk.
    curves = {}
    for band in FILTER_BANDS:
        mag = all_models_dict[MODEL_NAME](TIME, **params, output_format='magnitude', bands=[band])
        curves[band] = np.asarray(mag - mu, dtype=np.float32)
    return sample_id, params, curves
# Main simulation function
def simulate_kilonova(
    N_SIM=100,
    MODEL_NAME='two_component_kilonova_model',
    DL_Mpc=259.0,
    FILTER_BANDS=('lsstg', 'lsstr', 'lssti', 'lsstz'),
    TIME=None,
    save=True,
    grid_name=None,
    replace=True,
    dsn=None,
    chunk_size=250,
):
    """Generate a simulation grid, writing it straight into the grid store.

    There is no intermediate file. The simulator opens a connection, registers
    the grid, and COPYs each chunk of finished lightcurves into
    ``kn_grid_lightcurve`` as it is produced; when the last chunk lands the grid
    is complete and queryable. The Parquet stage this replaces existed only to
    hand the numbers to a separate ingest pass, and cost a 6 GB file per rung
    plus a second full read of it.

    Grids are keyed by name, not by path::

        simulations_two_component_kilonova_model_259Mpc

    with the luminosity distance carried in ``kn_grid_axis.distance_mpc`` --
    read from a column now rather than parsed back out of a filename, so
    ``KilonovaScorer/generate_rung.py`` can build a grid at any distance and
    have it identified correctly with a single query.

    Requires ``TROVE_GRID_DSN`` (or an explicit ``dsn``) and a schema created by
    :func:`grid_db.ensure_schema`, which this calls.

    Parameters
    ----------
    N_SIM : int
        Number of light curves to draw from the prior.
    MODEL_NAME : str
        redback model name.
    DL_Mpc : float
        Luminosity distance to simulate at. Sets the redshift handed to the
        model, so a grid is only valid near this distance -- generate one per
        distance bin.
    FILTER_BANDS : sequence of str
        redback band names. Add IR/JWST bands here if you want them in the grid;
        the scorer ignores bands it cannot map.
    TIME : array-like or None
        Time samples in days. Defaults to 1000 points over 0-10 d. Note t=0 is
        undefined for these models and is dropped at load time.
    save : bool
        Write to the store. When False nothing is persisted and the finished
        lightcurves are returned in memory instead -- small test runs only.
    grid_name : str or None
        Name to store under. Defaults to
        ``simulations_{model}_{distance:.0f}Mpc``.
    replace : bool
        Delete any existing lightcurves for this grid name first. On by
        default: a shorter re-run would otherwise leave the tail of the
        previous one in place, indistinguishable from its own output.
    dsn : str or None
        Grid database connection string. Defaults to ``$TROVE_GRID_DSN``.
    chunk_size : int
        Samples simulated and written per batch. Peak memory is roughly
        ``chunk_size * len(FILTER_BANDS) * len(TIME) * 4`` bytes -- 76 MB at the
        default 250 across 38 bands and 1,000 epochs.

    Returns
    -------
    (result, grid_name) : (dict or None, str or None)
        When saving, ``result`` is a summary dict and ``grid_name`` is the key
        the grid was stored under. With ``save=False`` the summary carries the
        lightcurves themselves and the name is None.
    """
    FILTER_BANDS = list(FILTER_BANDS)
    if TIME is None:
        TIME = np.linspace(0, 10, 1000)
    TIME = np.asarray(TIME, dtype=float)
    z = z_at_value(cosmo.luminosity_distance, DL_Mpc*u.Mpc).value
    mu = 5 * np.log10(DL_Mpc*1e6) - 5

    ncores = min(6, multiprocessing.cpu_count() - 1)
    print(f"🕹 {N_SIM} samples x {len(FILTER_BANDS)} bands x {len(TIME)} epochs "
          f"= {N_SIM * len(FILTER_BANDS) * len(TIME):,} magnitudes, on {ncores} cores",
          flush=True)

    # Must happen BEFORE the Pool starts. sncosmo fetches a bandpass the first
    # time it is used and caches it under ~/.astropy/cache/sncosmo. With a cold
    # cache every worker reaches the same uncached band at the same moment and
    # they all download it to the same path concurrently; one of them reads a
    # partially written file, gets an empty transmission array, and the run
    # dies far downstream with
    #
    #     ValueError: zero-size array to reduction operation maximum which has
    #     no identity
    #
    # which names neither the band nor the cache. Touching every bandpass once,
    # serially, in the parent makes the workers' lookups pure cache hits.
    _prewarm_bandpasses(FILTER_BANDS)
    patch_redback_time_cap()

    if not save:
        # Small test runs only -- everything is held in memory.
        with multiprocessing.Pool(ncores) as pool:
            out = pool.starmap(
                simulate_single_sample,
                [(i, MODEL_NAME, TIME, FILTER_BANDS, z, mu) for i in range(N_SIM)],
            )
        print("✅ Simulation complete!", flush=True)
        return {
            "time": TIME,
            "distance_mpc": float(DL_Mpc),
            "samples": {sid: curves for sid, _params, curves in out},
            "params": {sid: params for sid, params, _curves in out},
        }, None

    from . import grid_db

    grid_name = grid_name or f"simulations_{MODEL_NAME}_{DL_Mpc:.0f}Mpc"
    grid_db.ensure_schema(dsn)
    grid_db.begin_grid(
        grid_name, TIME, float(DL_Mpc), int(N_SIM), replace=replace, dsn=dsn
    )
    print(f"📇 writing to grid store as '{grid_name}'", flush=True)

    n_written = 0
    t0 = time.time()
    try:
        with multiprocessing.Pool(ncores) as pool:
            for start in range(0, N_SIM, chunk_size):
                stop = min(start + chunk_size, N_SIM)
                out = pool.starmap(
                    simulate_single_sample,
                    [(i, MODEL_NAME, TIME, FILTER_BANDS, z, mu) for i in range(start, stop)],
                )
                # Sorted so a chunk's rows go in by sample_id. Not required by
                # the schema, but it keeps the physical order of the table
                # aligned with sample_id, which is what the read path scans.
                out.sort(key=lambda r: r[0])
                sample_ids = [sid for sid, _p, _c in out]
                for band in FILTER_BANDS:
                    block = np.stack([curves[band] for _s, _p, curves in out])
                    grid_db.write_lightcurves(
                        grid_name, band, sample_ids, block, dsn=dsn
                    )
                n_written += len(sample_ids)
                del out

                elapsed = time.time() - t0
                rate = stop / elapsed
                print(f"   {stop}/{N_SIM} samples  {n_written * len(FILTER_BANDS):,} "
                      f"lightcurves  {elapsed/60:.1f} min elapsed, "
                      f"~{(N_SIM-stop)/rate/60:.1f} min left", flush=True)
    except BaseException:
        # A half-written grid that still answers queries is worse than none:
        # scoring would silently use whatever fraction landed. Drop it.
        try:
            grid_db.drop_grid(grid_name, dsn=dsn)
            print(f"⚠ removed the partial grid '{grid_name}'", flush=True)
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.exception("could not clean up partial grid %s", grid_name)
        raise

    summary = {
        "grid": grid_name,
        "distance_mpc": float(DL_Mpc),
        "n_samples": n_written,
        "n_time": int(TIME.size),
        "bands": {b: n_written for b in FILTER_BANDS},
    }
    print(f"💾 Stored {grid_name}: {n_written:,} samples x {len(FILTER_BANDS)} bands "
          f"in {(time.time()-t0)/60:.1f} min", flush=True)
    print("✅ Simulation complete!", flush=True)
    return summary, grid_name
