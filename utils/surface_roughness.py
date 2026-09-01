import numpy as np
from scipy import ndimage
from scipy.ndimage import distance_transform_edt
from scipy.ndimage import gaussian_filter


import pandas as pd
import matplotlib.pyplot as plt


def calculate_areal_roughness(
    z: np.ndarray,
    dx: float = 1.0,
    dy: float = 1.0,
    level: bool = True,
    filter_cutoff: float = None
) -> dict:
    """
    Calculate ISO 25178 areal surface roughness parameters from 2D profilometry data.

    Parameters
    ----------
    z : np.ndarray, shape (M, N)
        2D array of surface heights
    dx : float
        Pixel/step spacing in x-direction (same units as z)
    dy : float
        Pixel/step spacing in y-direction (same units as z)
    level : bool
        If True, subtract least-squares plane fit before calculation
    filter_cutoff : float or None
        Gaussian filter cutoff wavelength (same units as dx/dy).
        If None, no filtering is applied.

    Returns
    -------
    dict with keys: Sa, Sq, Sz, Sp, Sv, Ssk, Sku
    """

    z = np.array(z, dtype=float)

    # --- Step 1: Level surface (subtract best-fit plane) ---
    if level:
        z = _subtract_plane(z, dx, dy)

    # --- Step 2: Apply Gaussian filter (separate roughness from waviness) ---
    if filter_cutoff is not None:
        z = _apply_gaussian_filter(z, dx, dy, filter_cutoff)

    # --- Step 3: Calculate mean and deviations ---
    z_mean = np.mean(z)
    dev = z - z_mean

    # --- Step 4: Compute parameters ---
    N = dev.size

    Sa  = np.mean(np.abs(dev))
    Sq  = np.sqrt(np.mean(dev**2))
    Sp  = np.max(dev)               # highest peak
    Sv  = np.abs(np.min(dev))       # deepest valley
    Sz  = Sp + Sv                   # max height
    Ssk = np.mean(dev**3) / Sq**3  # skewness
    Sku = np.mean(dev**4) / Sq**4  # kurtosis

    return {
        "Sa":  Sa,
        "Sq":  Sq,
        "Sz":  Sz,
        "Sp":  Sp,
        "Sv":  Sv,
        "Ssk": Ssk,
        "Sku": Sku,
    }


# ── Helper Functions ──────────────────────────────────────────────────────────

def _subtract_plane(z: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """
    Fit and subtract a least-squares plane from the height data.
    Removes tilt and offset (form correction).
    """
    M, N = z.shape

    # Build coordinate arrays
    x = np.arange(N) * dx
    y = np.arange(M) * dy
    X, Y = np.meshgrid(x, y)

    # Design matrix for plane: z = a*x + b*y + c
    A = np.column_stack([
        X.ravel(),
        Y.ravel(),
        np.ones(M * N)
    ])

    # Least-squares solution
    coeffs, _, _, _ = np.linalg.lstsq(A, z.ravel(), rcond=None)
    plane = (coeffs[0] * X + coeffs[1] * Y + coeffs[2])

    return z - plane


def _apply_gaussian_filter(
    z: np.ndarray,
    dx: float,
    dy: float,
    cutoff: float
) -> np.ndarray:
    """
    High-pass Gaussian filter to isolate roughness from waviness.
    Subtracts a Gaussian-smoothed version of the surface.

    Parameters
    ----------
    cutoff : float
        Spatial cutoff wavelength (λc) in same units as dx/dy.
        Features larger than this are removed.
    """
    sigma_x = (cutoff / dx) / (2 * np.pi)
    sigma_y = (cutoff / dy) / (2 * np.pi)

    # Low-frequency component (waviness)
    z_smooth = ndimage.gaussian_filter(z, sigma=[sigma_y, sigma_x])

    # High-frequency component (roughness)
    return z - z_smooth

def roughness_from_dataframe(
    df: pd.DataFrame,
    x_col: str = "X",
    y_col: str = "Y",
    z_col: str = "Height",
    level: bool = True,
    filter_cutoff: float = None
) -> dict:
    """
    Calculate ISO 25178 areal roughness parameters from a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with columns for X, Y, and Height
    x_col, y_col, z_col : str
        Column names for coordinates and height
    level : bool
        If True, subtract least-squares plane fit
    filter_cutoff : float or None
        Gaussian filter cutoff wavelength (same units as X/Y)

    Returns
    -------
    dict containing roughness parameters and the reconstructed 2D grid
    """

    # --- Step 1: Extract unique coordinates ---
    x_vals = np.sort(df[x_col].unique())
    y_vals = np.sort(df[y_col].unique())

    dx = np.mean(np.diff(x_vals))
    dy = np.mean(np.diff(y_vals))

    print(f"Grid size : {len(x_vals)} x {len(y_vals)}")
    print(f"X spacing : {dx:.4g}")
    print(f"Y spacing : {dy:.4g}")
    print(f"X range   : {x_vals.min():.4g} → {x_vals.max():.4g}")
    print(f"Y range   : {y_vals.min():.4g} → {y_vals.max():.4g}")

    # --- Step 2: Pivot into 2D grid ---
    # pivot_table handles duplicate/missing points gracefully
    grid = df.pivot_table(
        index=y_col,
        columns=x_col,
        values=z_col,
        aggfunc="mean"       # average if duplicate points exist
    )
    z = grid.values*1e-9          # shape: (n_y, n_x)

    # --- Step 3: Check for missing values ---
    n_missing = np.isnan(z).sum()
    if n_missing > 0:
        print(f"Warning: {n_missing} missing points — filling by interpolation")
        # Simple inpainting via nearest-neighbour
        from scipy.ndimage import distance_transform_edt
        mask = np.isnan(z)
        if mask.any():
            # For each NaN pixel, find the index of the nearest non-NaN pixel
            _, nearest_idx = distance_transform_edt(mask, return_indices=True)
            z[mask] = z[nearest_idx[0][mask], nearest_idx[1][mask]]

    # --- Step 4: Calculate roughness ---
    params = calculate_areal_roughness(z, dx=dx, dy=dy,
                                       level=level,
                                       filter_cutoff=filter_cutoff)
    params["grid"] = z        # attach grid for plotting
    params["x_vals"] = x_vals
    params["y_vals"] = y_vals

    return params

def total_variation_metrics(
    df: pd.DataFrame,
    x_col: str = "X",
    y_col: str = "Y",
    z_col: str = "Height",
    waviness_sigma: float = 20.0
) -> dict:
    """
    Compute multi-scale surface non-uniformity metrics from a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with columns for X, Y, and Height
    x_col, y_col, z_col : str
        Column names for coordinates and height
    waviness_sigma : float
        Gaussian filter sigma (in pixels) for separating waviness from roughness.
        Larger values → broader waviness component captured.

    Returns
    -------
    dict containing Sq at each scale, variance contributions, NUI, and grids
    """

    # --- Step 1: Pivot into 2D grid (same as roughness_from_dataframe) -------
    x_vals = np.sort(df[x_col].unique())
    y_vals = np.sort(df[y_col].unique())

    dx = np.mean(np.diff(x_vals))
    dy = np.mean(np.diff(y_vals))

    print(f"Grid size : {len(x_vals)} x {len(y_vals)}")
    print(f"X spacing : {dx:.4g}")
    print(f"Y spacing : {dy:.4g}")
    print(f"X range   : {x_vals.min():.4g} → {x_vals.max():.4g}")
    print(f"Y range   : {y_vals.min():.4g} → {y_vals.max():.4g}")

    grid = df.pivot_table(
        index=y_col,
        columns=x_col,
        values=z_col,
        aggfunc="mean"
    )
    z = grid.values.astype(float)*1e-9     # shape: (n_y, n_x)

    # --- Step 2: Handle missing values ---------------------------------------
    n_missing = np.isnan(z).sum()
    if n_missing > 0:
        print(f"Warning: {n_missing} missing points — filling by interpolation")
        mask = np.isnan(z)
        if mask.any():
            # For each NaN pixel, find the index of the nearest non-NaN pixel
            _, nearest_idx = distance_transform_edt(mask, return_indices=True)
            z[mask] = z[nearest_idx[0][mask], nearest_idx[1][mask]]

    # --- Step 3: Decompose surface -------------------------------------------
    # Form: best-fit plane
    M, N = z.shape
    X_grid, Y_grid = np.meshgrid(np.arange(N) * dx, np.arange(M) * dy)
    A = np.column_stack([X_grid.ravel(), Y_grid.ravel(), np.ones(M * N)])
    coeffs, _, _, _ = np.linalg.lstsq(A, z.ravel(), rcond=None)
    form = (coeffs[0] * X_grid + coeffs[1] * Y_grid + coeffs[2])

    # Waviness: low-pass filter of form-subtracted surface
    residual_after_form = z - form
    waviness = gaussian_filter(residual_after_form, sigma=waviness_sigma)

    # Roughness: residual after removing form and waviness
    roughness = residual_after_form - waviness

    # --- Step 4: Compute Sq at each scale ------------------------------------
    def sq(arr):
        dev = arr - np.nanmean(arr)
        return np.sqrt(np.nanmean(dev**2))

    Sq_total     = sq(z)
    Sq_form      = sq(form)
    Sq_waviness  = sq(waviness)
    Sq_roughness = sq(roughness)

    # --- Step 5: Variance contributions --------------------------------------
    # Variances are additive for orthogonal components
    Sq_total_recon = np.sqrt(Sq_form**2 + Sq_waviness**2 + Sq_roughness**2)

    pct_form      = 100 * Sq_form**2      / Sq_total_recon**2
    pct_waviness  = 100 * Sq_waviness**2  / Sq_total_recon**2
    pct_roughness = 100 * Sq_roughness**2 / Sq_total_recon**2

    # --- Step 6: Non-Uniformity Index ----------------------------------------
    # NUI = 1  → pure roughness, no trends
    # NUI >> 1 → strong form/waviness present
    NUI = Sq_total / Sq_roughness if Sq_roughness > 0 else np.inf

    return {
        # Absolute Sq values
        "Sq_total":     Sq_total,
        "Sq_form":      Sq_form,
        "Sq_waviness":  Sq_waviness,
        "Sq_roughness": Sq_roughness,

        # Relative variance contributions
        "pct_form":      pct_form,
        "pct_waviness":  pct_waviness,
        "pct_roughness": pct_roughness,

        # Overall non-uniformity
        "NUI": NUI,

        # Grids for downstream plotting
        "_grid":      z,
        "_form":      form,
        "_waviness":  waviness,
        "_roughness": roughness,
        "_x_vals":    x_vals,
        "_y_vals":    y_vals,
    }
    
def visualize_roughness(results):
    
    z_grid   = results["grid"]
    x_vals   = results["x_vals"] * 1e6    # convert to µm for display
    y_vals   = results["y_vals"] * 1e6

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # Raw surface
    im0 = axes[0].pcolormesh(x_vals, y_vals, z_grid * 1e9, cmap="viridis")
    axes[0].set_title("Raw Surface (from DataFrame)")
    axes[0].set_xlabel("X (µm)")
    axes[0].set_ylabel("Y (µm)")
    plt.colorbar(im0, ax=axes[0], label="Height (nm)")

    # Height distribution
    z_dev = z_grid - np.mean(z_grid)
    axes[1].hist(z_dev.ravel() * 1e9, bins=80,
                color="steelblue", edgecolor="white", linewidth=0.3)
    axes[1].set_xlabel("Height deviation (nm)")
    axes[1].set_ylabel("Count")
    axes[1].set_title(
        f"Height Distribution\n"
        f"Sa={results['Sa']*1e9:.2f} nm  "
        f"Sq={results['Sq']*1e9:.2f} nm  "
        f"Ssk={results['Ssk']:.3f}"
    )

    plt.tight_layout()
    plt.show()
    
def plot_nonuniformity(df, x_col="X", y_col="Y", z_col="Height"):

    tv  = total_variation_metrics(df, x_col, y_col, z_col)
    #psd = compute_psd(df, x_col, y_col, z_col)

    fig = plt.figure(figsize=(16, 10))
    gs  = fig.add_gridspec(2, 4, hspace=0.4, wspace=0.35)

    # ── Surface decomposition maps ─────────────────────────────────────────
    panels = [
        (tv["_grid"],      "Total Surface",    "viridis"),
        (tv["_form"],  "Form",         "viridis"),
        (tv["_waviness"],  "Waviness",         "plasma"),
        (tv["_roughness"], "Roughness",        "RdBu_r"),
    ]
    for col, (data, title, cmap) in enumerate(panels):
        ax = fig.add_subplot(gs[0, col])
        im = ax.imshow(data * 1e9, cmap=cmap, origin="lower")
        ax.set_title(title)
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        plt.colorbar(im, ax=ax, label="nm")

    # ── Variance contributions bar chart ───────────────────────────────────
    ax = fig.add_subplot(gs[1, 0])
    labels = ["Form", "Waviness", "Roughness"]
    values = [tv["pct_form"], tv["pct_waviness"], tv["pct_roughness"]]
    colors = ["#e74c3c", "#f39c12", "#2ecc71"]
    bars = ax.bar(labels, values, color=colors, edgecolor="white")
    ax.bar_label(bars, fmt="%.1f%%", padding=3)
    ax.set_ylabel("% of Total Variance")
    ax.set_title(f"Variance Decomposition\nNUI = {tv['NUI']:.2f}")
    ax.set_ylim(0, 110)

    # ── 1D PSD ────────────────────────────────────────────────────────────
    #ax = fig.add_subplot(gs[1, 1:])
    #wl = psd["wavelength"]
    #valid = np.isfinite(wl)
    #ax.loglog(wl[valid] * 1e6, psd["psd1d"][valid], color="steelblue", lw=2)
    #ax.set_xlabel("Wavelength (µm)")
    #ax.set_ylabel("PSD (nm² · µm²)")
    #ax.set_title("Power Spectral Density\n(peak = dominant spatial scale)")
    #ax.grid(True, which="both", alpha=0.3)

    # Annotate dominant wavelength
    #peak_idx = np.nanargmax(psd["psd1d"])
    #peak_wl  = wl[peak_idx] * 1e6
   # ax.axvline(peak_wl, color="red", linestyle="--", alpha=0.7,
   #            label=f"Dominant λ = {peak_wl:.1f} µm")
   # ax.legend()

    #plt.suptitle("Surface Non-Uniformity Analysis", fontsize=14, fontweight="bold")
    #plt.show()

    # ── Print summary ─────────────────────────────────────────────────────
    print("\nSurface Non-Uniformity Summary")
    print("=" * 40)
    print(f"  Sq total     = {tv['Sq_total']*1e9:.3f} nm")
    print(f"  Sq form      = {tv['Sq_form']*1e9:.3f} nm  ({tv['pct_form']:.1f}%)")
    print(f"  Sq waviness  = {tv['Sq_waviness']*1e9:.3f} nm  ({tv['pct_waviness']:.1f}%)")
    print(f"  Sq roughness = {tv['Sq_roughness']*1e9:.3f} nm  ({tv['pct_roughness']:.1f}%)")
    print(f"\n  Non-Uniformity Index (NUI) = {tv['NUI']:.2f}")
   # print(f"  Dominant wavelength        = {peak_wl:.1f} µm")
   
def process_etching_surface(fn):
    surface = pd.read_csv(fn, delimiter='\t')
    surface.columns = ['Electrode #','X','Y','Height']
    surface.loc[surface['Electrode #'] != 0, 'Height'] = np.nan
    roughness_result = roughness_from_dataframe(surface, x_col='X', y_col='Y', z_col='Height')
    nonuniformity_result = total_variation_metrics(surface, x_col='X', y_col='Y', z_col='Height')
    
    roughness = roughness_result['Sa']
    nonuniformity = nonuniformity_result['Sq_total']
    
    return({'roughness': float(roughness*1e9), 'nonuniformity': float(nonuniformity*1e9)})