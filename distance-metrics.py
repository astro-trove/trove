import io
from typing import Iterable
import json
import numpy as np
import pandas as pd
import warnings
from tqdm import tqdm
from candidate_vetting.vet import host_association
from scoring.scoring import host_distance_match, get_distance_score_diagnostic
from tom_nonlocalizedevents.models import NonLocalizedEvent, EventCandidate
from django.conf import settings
from trove_targets.models import Target
from tom_targets.models import TargetExtra

OVERWRITE = False
JNAME = "distance-scoring-metrics.json"
KEY = "Hybrid BC/Tophat"

class NpEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super(NpEncoder, self).default(obj)

nle = NonLocalizedEvent.objects.get(id=20113)
ecs = EventCandidate.objects.filter(nonlocalizedevent=nle)

if OVERWRITE:
    all_metrics = {}
    for ec in ecs:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            host_df = host_association(ec.target_id)
            host_df = host_distance_match(host_df, ec.target_id, nle.event_id)
            metrics = get_distance_score_diagnostic(host_df, ec.target_id, nle.event_id)
        all_metrics[ec.target.name] = metrics
    with open(JNAME, "w") as f:
        json.dump(all_metrics, f, cls=NpEncoder, indent=4)
else:
    with open(JNAME, "r") as f:
        all_metrics = json.load(f)

toplot = {
    "dist":[],
    "dist_err":[],
    "dist_type":[],
    "score":[]
}
        
for target_name in all_metrics.keys():

    if not len(all_metrics[target_name]):
        continue
    
    target = Target.objects.get(name=target_name)
    host_df = pd.read_json(io.StringIO(TargetExtra.objects.filter(target=target, key="Host Galaxies").first().value))
    id_ = all_metrics[target_name][KEY][1]
    if id_ is None:
        dist = settings.COSMO.luminosity_distance(target.redshift).value
        dist_err = 1e-3
    else:
        row = host_df[host_df.ID == id_]
        dist = row.Dist.values[0]
        dist_err = row.DistErr.values[0]

    toplot["dist"].append(dist)
    toplot["dist_err"].append(np.mean(dist_err) if isinstance(dist_err, Iterable) else dist_err )
    toplot["dist_type"].append(all_metrics[target_name][KEY][2])
    toplot["score"].append(all_metrics[target_name][KEY][0])

df = pd.DataFrame(toplot)

# now plot
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
fig, ax = plt.subplots()

marker_dict = {
    'photo-z':("photo-z", "o"),
    'spec-z':("spec-z", "^"),
    'redshift':("target-spec-z", "s"),
    'ind':("z-ind.", "P")
}

# Define vmin and vmax based on your data range
vmin, vmax = 1e-3, 1

# Create LogNorm instance (this is the key step)
norm = LogNorm(vmin=vmin, vmax=vmax)

for lab,grp in df.groupby("dist_type"): 
    lab, mark = marker_dict[lab]
    cbar = ax.scatter(
        grp.dist,
        grp.score,
        c=grp.dist_err,
        alpha=0.3,
        zorder=10,
        norm=norm,
        cmap="viridis",
        marker = mark,
        label = lab
    )

#x.set_yscale("log"); ax.set_ylim(1e-3, 1)
ax.set_xscale("log")

from scipy.stats import norm
x = np.logspace(np.log10(9), 4.5, 10000)
gw_pdf = norm.pdf(x=x, loc=93, scale=27)
ax.plot(x, gw_pdf/np.max(gw_pdf), color="red", zorder=0, label="GW Distance")

ax.set_ylabel("Hybrid Score")
ax.set_xlabel("Distance (Mpc)")

ax.legend(frameon=True)

fig.colorbar(cbar, label="Distance Error (Mpc)", extend="min")
fig.savefig("S251112cm-distance-scores-hybrid.png")
