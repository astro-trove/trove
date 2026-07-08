import numpy as np
from scipy.special import erfc

from scipy.integrate import trapezoid
from scipy.stats import norm, rv_continuous

from astropy.utils.introspection import minversion

if minversion(np, "2.0.0"):
    np_trapz_fn = np.trapezoid
else:
    np_trapz_fn = np.trapz  # np.trapz is deprecated in numpy >2.0.0

class AsymmetricGaussian(rv_continuous):
    """
    Custom Asymmetric Gaussian distribution for uneven uncertainties
    """

    def _pdf_unnorm(self, x, mean, unc_minus, unc_plus):
        """**Unnormalized** asymmetric Gaussian PDF"""
        # piecewise return a Gaussian depending on the side of the mean you are on
        where_minus = np.where(x < mean)[0]
        where_plus = np.where(x >= mean)[0]

        minus_dist = np.exp(
            -0.5 * ((x[where_minus] - mean[where_minus]) / unc_minus[where_minus]) ** 2
        )  # Left side Gaussian-like
        plus_dist = np.exp(
            -0.5 * ((x[where_plus] - mean[where_plus]) / unc_plus[where_plus]) ** 2
        )  # Right side Gaussian-like

        return np.concatenate((minus_dist, plus_dist))

    def _pdf(self, x, mean, unc_minus, unc_plus, integ_a, integ_b):
        """**Normalized** asymmetric Gaussian PDF"""
        # unclear why, but even when floats are passed to this function for
        # args mean, unc_minus, unc_plus, integ_a, integ_b, they become lists
        # of the same value repeated len(x) times

        # numerically integrate asymmetric Gaussian, for normalization
        integ_x = np.linspace(integ_a[0], integ_b[0], x.shape[0])
        integ = np_trapz_fn(
            y=self._pdf_unnorm(integ_x, mean, unc_minus, unc_plus), x=integ_x
        )
        integ_norm = 1 / integ

        # return unnormalized PDF multiplied by normalization factor
        return self._pdf_unnorm(x, mean, unc_minus, unc_plus) * integ_norm

## Distance Scoring Helper Functions

# finally, compute the Bhattacharyya coefficient for the overlap of these
# two distributions. https://en.wikipedia.org/wiki/Bhattacharyya_distance
# This coefficient is non-parametric which is good for our Asymmetric Gaussian
# Original paper: http://www.jstor.org/stable/25047806
def bc(pdf1, pdf2, x):
    """The bhattacharyya coefficient of PDF1 and PDF2, defined over x"""
    return trapezoid(np.sqrt(pdf1*pdf2), x=x)

def bc_norm_median_asymmetric(ref_pdf, test_pdf, mean1, unc_minus, unc_plus, x):
    shift_from_mean = (unc_plus-unc_minus)/2

    maxtest_pdf = AsymmetricGaussian().pdf(
        x,
        mean=mean1 - shift_from_mean,
        unc_minus=unc_minus,
        unc_plus=unc_plus,
        integ_a=1e-9,
        integ_b=x[-1],
    )

    bc_true = bc(ref_pdf, test_pdf, x)
    bc_max = bc(ref_pdf, maxtest_pdf, x)

    return bc_true/bc_max

def zscore(mean1, mean2, std1, pdf=norm.pdf):
    zscore = (mean2 - mean1) / std1
    return pdf(zscore) / pdf(0)

def resampled_zscore(mean1, mean2, std1, unc_minus, unc_plus):
    # Samples 1M values from an asymmetric (two-piece) gaussian with the given
    # mean and uncertainty values, via direct half-normal sampling. AsymmetricGaussian's
    # generic scipy .rvs() can't be used here since it numerically inverts the
    # CDF via scalar calls to _pdf(), which assumes vectorized integ_a/integ_b.
    n = 1_000_000
    p_left = unc_minus / (unc_minus + unc_plus)
    is_left = np.random.random(n) < p_left
    magnitudes = np.abs(np.random.normal(size=n))
    test_rand_values = np.where(
        is_left,
        mean2 - magnitudes * unc_minus,
        mean2 + magnitudes * unc_plus,
    )
    # Measures the z-scores of all of these values with respect to the provided mean
    resampled_zscores = zscore(mean1, test_rand_values, std1)
    # Returns the median of all of the z-scores
    return np.quantile(resampled_zscores, 0.5)

# Self-Entropy
# Sum of -p_i*log(p_i)
# Add a minimum bound to prevent log(0) errors

def shannon_entropy(pdf, eps=1e-85):
    pdf = np.asarray(pdf, dtype=float)
    # Normalization
    pdf = pdf / np.sum(pdf)
    pdf = np.clip(pdf, eps, None)
    return float(np.sum(-pdf * np.log2(pdf)))

# Relative Entropy Calculations
# D(P || Q) = sum_i p_i * log2(p_i / q_i)
# This is non-negative when both inputs are valid probability distributions.
def relative_entropy(p, q, eps=1e-85):
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)

    if p.shape != q.shape:
        raise ValueError("p and q must have the same shape")

    p = p / np.sum(p)
    q = q / np.sum(q)

    p = np.clip(p, eps, None)
    q = np.clip(q, eps, None)

    return float(np.sum(p * np.log2(p / q)))

# JSD (Jensen-Shannon Divergence)
# JSD(P || Q) = 0.5 * D(P || M) + 0.5 * D(Q || M)
# where M = 0.5 * (P + Q). With base-2 logs, JSD is bounded between 0 and 1.
# 0 means that the distributions are very similar, and 1 means that they are completely different
def jsd(p, q, eps=1e-85):
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)

    # Normalization of PDFs
    p = p / np.sum(p)
    q = q / np.sum(q)

    m = 0.5 * (p + q)
    jsd_value = 0.5 * relative_entropy(p, m, eps=eps) + 0.5 * relative_entropy(q, m, eps=eps)
    return float(jsd_value)

# Consider a better way to measure how uniform the function is
# Current problem is that heavily skewed PDFs are getting a very low score in compare uniform
# even though they are fairly well-known, and this is inflating the asymmetric_pdf_skewed scores
def compare_uniform(p, eps=1e-15):
    # Instead of naively generating the uniform distribution across p's entire domain
    # only generate uniform_dist across the region where p > eps
    uniform_dist = np.full_like(p, 1.0 / len(p))
    return jsd(uniform_dist, p)

# This is not really giving very good results
# Entropy comparison to uniform distribution
def entropy_comp_uniform(p):
    # np.log2(len(p)) is the entropy of a completely uniform distribution
    return 1 - (shannon_entropy(p) / np.log2(len(p)))

def raw_zscore(mean1, mean2, std):
    return (mean2 - mean1) / std

def information_metric(test_pdf, base_pdf):
    # 0 --> Close to each other
    # 1 --> Far from each other
    jsd_val = jsd(test_pdf, base_pdf)
    # 0 --> Close to uniform 
    # 1 --> Far from uniform
    uni_comp = compare_uniform(test_pdf)

    # Currently, information theory metrics alone does not give any information
    # about alignment or spatial relations, so we need to combine these
    # The main problem is coming from delta functions, which are localized at the mean of the 
    # target distribution, but because they don't have the target distribution shape, they are 
    # getting penalized for it
    # Prioritize alignment score, fine_tune these weights
    # Should inform_score be weighted by uni_comp?
    # Reconsider some of these metrics
    return (1.0 - (uni_comp*jsd_val))

def conditional_scoring(
    test_pdf, base_pdf, mean1, mean2, base_std,
    uni_thresh=0.2, uni_branch_coef=0.05,
    z_thresh=0.3, z_branch_weights=(0.95, 0.05),
    far_branch_weights=(0.9, 0.1),
):
    z_score = raw_zscore(mean1, mean2, base_std)

    # 0 --> Close to each other
    # 1 --> Far from each other
    jsd_val = jsd(test_pdf, base_pdf)
    # 0 --> Close to uniform
    # 1 --> Far from uniform
    uni_comp = compare_uniform(test_pdf)

    # Currently, information theory metrics alone does not give any information
    # about alignment or spatial relations, so we need to combine these
    # The main problem is coming from delta functions, which are localized at the mean of the
    # target distribution, but because they don't have the target distribution shape, they are
    # getting penalized for it
    # Prioritize alignment score, fine_tune these weights
    # Should inform_score be weighted by uni_comp?
    # Reconsider some of these metrics
    inform_score = 1 - (jsd_val)
    zscored = zscore(mean1, mean2, base_std)

    # Conditional Scoring
    if uni_comp < uni_thresh:
        # Should this just be 1 or some small deviation?
        # No small deviation because if uni_comp is that low, then we don't know anything about the function
        # Potentially depend on the threshold?
        # Naive implementation for now
        final_score = 1 - uni_comp
    elif abs(z_score) < z_thresh:
        # Consider introducing a little bit more information about how the distribution matches the target
        # Currently losing a bit too much
        final_score = z_branch_weights[0]*(zscored) + z_branch_weights[1]*(inform_score)
    else:
        final_score = far_branch_weights[0]*inform_score + far_branch_weights[1]*zscored

    return final_score

def consistency_probability(mean1, mean2, std1, unc_minus, unc_plus, wt=2):
    if mean2 < mean1:
        # then the relevant tail is the upper side of the asymmetric distribution
        std2 = unc_plus
    else:
        std2 = unc_minus

    sigma_diff = wt*np.sqrt(2*(std1**2 + std2**2))
    mean_diff = np.abs(mean1 - mean2)
    return erfc(mean_diff/sigma_diff)

def cons_prob_3(mean1, mean2, std1, unc_minus, unc_plus, wt=2):
    mean_diff = mean2 - mean1
    host_scale = np.sqrt(unc_minus**2 + unc_plus**2)
    z_host = mean_diff / host_scale

    w_minus = 0.5 * erfc(-z_host / np.sqrt(2))
    w_plus = 1 - w_minus

    var2 = w_minus * unc_minus**2 + w_plus * unc_plus**2

    sigma_diff = wt * np.sqrt(2 * (std1**2 + var2))
    return erfc((np.abs(mean_diff) / sigma_diff))

def hybrid_cons_prob(mean1, mean2, std1, unc_minus, unc_plus, wt=2, z_thresh=1.1, width_thresh=0.5):
    mean_diff = mean2 - mean1
    host_scale = np.sqrt(unc_minus**2 + unc_plus**2)
    z_gw = mean_diff / std1

    if np.abs(z_gw) < z_thresh and host_scale < width_thresh * std1:
        # Score depends only on the z-score, rescaled into [0.9, 1.0]
        print("Z Score only")
        return 1.0 - 0.1 * (np.abs(z_gw) / z_thresh)

    w_minus = 0.5 * erfc(-z_gw / np.sqrt(2))
    w_plus = 1 - w_minus

    sigma_minus = wt * np.sqrt(2 * (std1**2 + unc_minus**2))
    sigma_plus = wt * np.sqrt(2 * (std1**2 + unc_plus**2))
    score_minus = erfc(np.abs(mean_diff) / sigma_minus)
    score_plus = erfc(np.abs(mean_diff) / sigma_plus)

    return w_minus * score_minus + w_plus * score_plus