import numpy as np
from scipy.special import erfc, erf

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
def bc_slow(pdf1, pdf2, x):
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

    bc_true = bc_slow(ref_pdf, test_pdf, x)
    bc_max = bc_slow(ref_pdf, maxtest_pdf, x)

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

def robust_stats(pdf, x, p_lo=0.1587):
    # 15.87/84.13 reproduces standard 1 sigma for a symmetric gaussian distribution
    cdf = np.cumsum(pdf)
    cdf = cdf / cdf[-1]
    q_lo, q50, q_hi = np.interp([p_lo, 0.5, 1 - p_lo], cdf, x)
    z = norm.ppf(1 - p_lo)
    return q50, (q50 - q_lo) / z, (q_hi - q50) / z

def hybrid_cons_prob_v2(gw_pdf, host_pdf, x, phot_type, wt=1, z_thresh=2):
    # median location + robust MAD-family widths, but with the ORIGINAL
    # consistency_probability that discretely picks a single tail (no smooth
    # blend between tails like v2).
    m1, gm, gp = robust_stats(gw_pdf, x)
    mad1 = 0.5 * (gm + gp)                 # GW is symmetric -> gm == gp == sigma
    m2, s_minus, s_plus = robust_stats(host_pdf, x)

    med_diff = m2 - m1
    host_scale = np.sqrt(s_minus**2 + s_plus**2)
    z_gw = med_diff / mad1

    if phot_type == 'spec-z':
        # Score depends only on the (robust) z-score, rescaled into [0.9, 1.0]
        return 1.0 - 0.1 * (np.abs(z_gw) / z_thresh)

    if m2 < m1:
        # host median below the GW -> the upper tail is the one facing the GW
        mad2 = s_plus
    else:
        mad2 = s_minus

    sigma_diff = wt * np.sqrt(2 * (mad1**2 + mad2**2))

    # abs: med_diff can be negative, erfc of a negative would exceed 1
    score = erfc(np.abs(med_diff) / sigma_diff)
    return score

def normalization_prefactor(mean_gw, sigma_gw, mean_cand, sigma_cand_neg, sigma_cand_pos):
    prefactor_cand = np.sqrt(2/np.pi) * (sigma_cand_pos + sigma_cand_neg*erf(mean_cand/(sigma_cand_neg*np.sqrt(2))))**-1
    prefactor_gw = float(sigma_gw)**-1 * np.sqrt(2/np.pi) * (1 + erf(mean_gw/(sigma_gw*np.sqrt(2))))**-1
    return np.sqrt(prefactor_cand*prefactor_gw)

def a(sigma_gw, sigma_cand):
    return 1/sigma_gw**2 + 1/sigma_cand**2
        
def b(mean_gw, sigma_gw, mean_cand, sigma_cand):
    return mean_gw/sigma_gw**2 + mean_cand/sigma_cand**2

def c(mean_gw, sigma_gw, mean_cand, sigma_cand):
    return (mean_gw/sigma_gw)**2 + (mean_cand/sigma_cand)**2

def p(_a, _b, _c):
    return np.sqrt(np.pi/_a) * np.exp(-0.25*(_c - _b**2/_a))

def bc_integral_neg(mean_gw, sigma_gw, mean_cand, sigma_cand_neg):
    _a = a(sigma_gw, sigma_cand_neg)
    _b = b(mean_gw, sigma_gw, mean_cand, sigma_cand_neg)
    _c = c(mean_gw, sigma_gw, mean_cand, sigma_cand_neg)

    _p = p(_a, _b, _c)
    x0 = erf(-_b/(2*np.sqrt(_a)))
    x1 = erf(np.sqrt(_a)/2 * (mean_cand - _b/_a))
    return _p*(x1-x0)

def bc_integral_pos(mean_gw, sigma_gw, mean_cand, sigma_cand_pos):
    _a = a(sigma_gw, sigma_cand_pos)
    _b = b(mean_gw, sigma_gw, mean_cand, sigma_cand_pos)
    _c = c(mean_gw, sigma_gw, mean_cand, sigma_cand_pos)

    _p = p(_a, _b, _c)
    return _p*erfc(np.sqrt(_a)/2 * (mean_cand - _b/_a))

def bc(mean_gw, sigma_gw, mean_cand, sigma_cand_neg, sigma_cand_pos):
    lower_args = (mean_gw, sigma_gw, mean_cand, sigma_cand_neg)
    upper_args = (mean_gw, sigma_gw, mean_cand, sigma_cand_pos)
    norm = normalization_prefactor(mean_gw, sigma_gw, mean_cand, sigma_cand_neg, sigma_cand_pos)
    return norm*(bc_integral_neg(*lower_args) + bc_integral_pos(*upper_args))

def sigmoid(x, k=1):
    return 1 / (1 + np.exp(-k * x))

def smooth_tophat(x, a, b, k=1):
    return sigmoid(x - a, k) * (1 - sigmoid(x - b, k))

def smooth_tophat_score(galaxy_dist, gw_mean, gw_std, nsigma=2):
    return smooth_tophat(galaxy_dist, gw_mean-nsigma*gw_std, gw_mean+nsigma*gw_std)


def hybrid(gw_mean, galaxy_mean, gw_std, galaxy_std_minus, galaxy_std_plus):

    if galaxy_std_minus == 0 or galaxy_std_plus == 0:
        bc_score = 0 # this score should be computed in the sigmoid regime
    else:
        bc_score = bc(gw_mean, gw_std, galaxy_mean, galaxy_std_minus, galaxy_std_plus)

    tophat_score = smooth_tophat_score(galaxy_mean, gw_mean, gw_std)

    # this weight will be small for spec-z's and large for photo-z's   
    w = np.mean([galaxy_std_minus, galaxy_std_plus], axis=0) / gw_std
    max_w = np.ones(w.shape)
    w = np.min([max_w, w], axis=0)

    score = np.clip((1-w)*tophat_score + w*bc_score, 0, 1)

    print(f"\tBC={bc_score}", f"S={tophat_score}", f"w={w}", f"score={score}")

    return score

def tophat_score(galaxy_dist, gw_mean, gw_std, nsigma=2,
                 cliff=2.5,            # sigma where the steep drop happens
                 cliff_steepness=4,    # super-Gaussian order; higher = flatter top, sharper cliff
                 box_edge_score=0.95,  # target score at +/- nsigma (sets the in-box gradient)
                 tail_scale=6.0):      # sigma-scale of the heavy tail
    z = (galaxy_dist - gw_mean) / gw_std
    u = np.abs(z)
    alpha = np.log(box_edge_score) / nsigma**2
    tilt = np.exp(alpha * u**2)        # (A) gentle in-box gradient
    core = np.exp(-(u / cliff)**(2 * cliff_steepness)) # (B) flat plateau + steep cliff
    tail = 1.0 / (1.0 + (u / tail_scale)**2)           # (C) heavy tail
    # Find where this value gives desirable results
    weight = 0.98
    return (weight) * tilt * core + (1 - weight) * tail

def sigma_ratio(gw_std, sigma_minus, sigma_plus):
    # original: naive unweighted average of the two tails
    return np.mean([sigma_minus, sigma_plus], axis=0) / gw_std

# Mess around with the logistic k parameter
# Must have some physical reasoning for why the k-value is chosen
def weight_logistic(r, r0=1.0, k=4.0):
    # Soft threshold: stays ~0 (trust top-hat) until r ~ r0, then switches on.
    return 1.0 / (1.0 + np.exp(-k * (r - r0)))

# Key improvements are higher scores for delta_functions closer to the mean
def hybrid_v3(gw_mean, galaxy_mean, gw_std, galaxy_std_minus, galaxy_std_plus,
              weight_fn=weight_logistic, verbose=True):
    if galaxy_std_minus == 0 or galaxy_std_plus == 0:
        bc_score = 0 # this score should be computed in the sigmoid regime
    else:
        bc_score = bc(gw_mean, gw_std, galaxy_mean, galaxy_std_minus, galaxy_std_plus)

    ts = tophat_score(galaxy_mean, gw_mean, gw_std)

    # sigma combination: naive unweighted average of the two tails
    r = sigma_ratio(gw_std, galaxy_std_minus, galaxy_std_plus)
    w = np.clip(weight_fn(r), 0.0, 1.0)

    # Bhattacharya Coefficient is good (Potentially work on this a bit though)
    # Non-linear weighting and tophat scoring needs improvement
    if verbose:
        print(f"\tBC={bc_score}", f"S={ts}", f"w={w}", f"score={(1-w)*ts + w*bc_score}")

    return np.clip((1-w)*ts + w*bc_score, 0, 1)