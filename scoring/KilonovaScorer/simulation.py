import sys
import time
import multiprocessing
from multiprocessing import Pool, cpu_count
import numpy as np
import pandas as pd
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

def arcade_progress_bar(current, total, bar_length=30):
    """
    Prints an arcade-style progress bar to the console.
    """
    percent = current / total
    filled_length = int(bar_length * percent)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    sys.stdout.write(f'\r[ {bar} ] {percent*100:6.2f}% ⬛')
    sys.stdout.flush()
    if current == total:
        sys.stdout.write('\n')

# Worker function must be top-level for multiprocessing
def simulate_single_sample(sample_id, MODEL_NAME, TIME, FILTER_BANDS, z, mu, RANDOM_SEED=42):
    import numpy as np
    import pandas as pd
    import redback
    from bilby.core.prior import Uniform
    from redback.model_library import all_models_dict

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

    rows = []
    for band in FILTER_BANDS:
        mag = all_models_dict[MODEL_NAME](TIME, **params, output_format='magnitude', bands=[band])
        abs_mag = mag - mu
        row_dict = {'sample_id': sample_id, 'band': band, 'time': TIME, 
                    'magnitude': mag, 'absolute_magnitude': abs_mag}
        for k, v in params.items():
            row_dict[k] = v
        rows.append(pd.DataFrame(row_dict))
    return pd.concat(rows, ignore_index=True)

# Main simulation function
def simulate_kilonova(
    N_SIM=100,
    MODEL_NAME='two_component_kilonova_model',
    DL_Mpc=259.0,
    FILTER_BANDS=('lsstg', 'lsstr', 'lssti', 'lsstz'),
    TIME=None,
    save=True,
    outdir=None,
    filename=None,
    compression='zstd',
    chunk_size=250,
    SAVE_CSV=None,
):
    """Generate a simulation grid and save it as Parquet.

    Grids are written to ``KilonovaScorer/grids/`` as Parquet, with the
    luminosity distance in the filename so
    :func:`KilonovaScorer.grids.grid_for_distance` can build a distance ladder
    from the directory:

        grids/simulations_two_component_kilonova_model_259Mpc.parquet

    Parquet rather than CSV because these are large and purely numeric -- a
    10k-sample grid is ~90M rows, which is ~1.4 GB as Parquet against ~20 GB as
    CSV, and loads in a fraction of a second with column pruning.

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
        Write the grid to disk. Results are streamed out in chunks, so peak
        memory stays bounded no matter how large the grid is.
    outdir, filename : path-like or None
        Override the destination. Defaults to the package ``grids/`` directory
        and the distance-tagged name above.
    chunk_size : int
        Samples simulated and written per batch. Peak memory is roughly
        ``chunk_size * len(FILTER_BANDS) * len(TIME)`` rows. The default keeps a
        38-band, 1000-epoch grid near 1 GB.
    SAVE_CSV : bool or None
        Deprecated. Ignored -- a full grid is ~20x larger as CSV and does not
        fit in memory as a single frame.

    Returns
    -------
    (final_df, path) : (pd.DataFrame or None, pathlib.Path or None)
        ``final_df`` is returned only when ``save`` is False (small test runs);
        when saving, results go straight to disk and ``None`` is returned so a
        multi-hundred-million-row grid is never held in RAM.
    """
    from pathlib import Path

    if TIME is None:
        TIME = np.linspace(0, 10, 1000)
    FILTER_BANDS = list(FILTER_BANDS)
    z = z_at_value(cosmo.luminosity_distance, DL_Mpc*u.Mpc).value
    mu = 5 * np.log10(DL_Mpc*1e6) - 5

    est_rows = N_SIM * len(FILTER_BANDS) * len(TIME)
    ncores = min(6, multiprocessing.cpu_count() - 1)
    print(f"🕹 {N_SIM} samples x {len(FILTER_BANDS)} bands x {len(TIME)} epochs "
          f"= {est_rows:,} rows, on {ncores} cores", flush=True)

    if not save:
        # Small test runs only -- everything is held in memory.
        with multiprocessing.Pool(ncores) as pool:
            dfs = pool.starmap(
                simulate_single_sample,
                [(i, MODEL_NAME, TIME, FILTER_BANDS, z, mu) for i in range(N_SIM)],
            )
        print("✅ Simulation complete!", flush=True)
        return pd.concat(dfs, ignore_index=True), None

    # ------------------------------------------------------------------
    # Streamed write. The previous implementation collected every sample in a
    # list and concatenated once, which needs ~40 GB for a 38-band 10k-sample
    # grid -- more RAM than the machine has. Simulating in chunks and appending
    # each as a Parquet row group bounds memory to one chunk.
    # ------------------------------------------------------------------
    import pyarrow as pa
    import pyarrow.parquet as pq

    from .grids import GRID_DIR

    outdir = Path(outdir) if outdir else GRID_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    name = filename or f"simulations_{MODEL_NAME}_{DL_Mpc:.0f}Mpc.parquet"
    path = outdir / name
    tmp = path.with_suffix(".parquet.partial")

    writer = None
    n_rows = 0
    t0 = time.time()
    try:
        with multiprocessing.Pool(ncores) as pool:
            for start in range(0, N_SIM, chunk_size):
                stop = min(start + chunk_size, N_SIM)
                dfs = pool.starmap(
                    simulate_single_sample,
                    [(i, MODEL_NAME, TIME, FILTER_BANDS, z, mu) for i in range(start, stop)],
                )
                chunk = pd.concat(dfs, ignore_index=True)
                del dfs
                table = pa.Table.from_pandas(chunk, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(tmp, table.schema, compression=compression)
                writer.write_table(table)
                n_rows += len(chunk)
                del chunk, table
                elapsed = time.time() - t0
                rate = stop / elapsed
                print(f"   {stop}/{N_SIM} samples  {n_rows:,} rows  "
                      f"{elapsed/60:.1f} min elapsed, ~{(N_SIM-stop)/rate/60:.1f} min left",
                      flush=True)
    finally:
        if writer is not None:
            writer.close()

    # Only publish under the real name once the whole grid is written, so an
    # interrupted run never leaves a truncated grid that looks complete.
    tmp.replace(path)
    print(f"💾 Saved {path} ({path.stat().st_size / 1e6:.0f} MB, {n_rows:,} rows) "
          f"in {(time.time()-t0)/60:.1f} min", flush=True)
    print("✅ Simulation complete!", flush=True)
    return None, path