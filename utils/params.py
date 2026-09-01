"""
Physical parameters and geometry helpers for the etching simulation.

Reactor geometry constants (R_chamber, H_chamber, etc.) are imported from
``config.py``.  Variable display-name mappings are also sourced from there.
This file retains the etching-specific grid setup and helper functions that
do not belong in the general config.
"""

import numpy as np
import shapely
from config import (
    R_CHAMBER, H_CHAMBER, R_WAFER, H_WAFER,
    VAR_TO_NAME, NAME_TO_VAR,
)

# ── ETCHING SIMULATION PARAMETERS ──────────────────────────────────────────────
thick_sample = 400.0       # Average sample thickness (nm)
dt = 0.1                   # Integration timestep (s)
T_max = 40                 # Total simulation time (s)
save_interval = 1          # Save every N steps

pixel_xy = 1e-4            # Grid spacing in X/Y (m)
noise_amp = 12.0e-6        # ± surface noise amplitude (m)
thick_metal = 300.0        # Metal layer thickness boundary (nm)
rate_ratio_lower = 0.1     # Etch rate ratio for lower layer
length_x = 1e-2            # X dimension (m)
length_y = 1e-2            # Y dimension (m)

# ── GRID DEFINITION ────────────────────────────────────────────────────────────
nx = int(length_x / pixel_xy) + 1
ny = int(length_y / pixel_xy) + 1
xi = np.linspace(-length_x / 2, length_x / 2, nx)
yi = np.linspace(-length_y / 2, length_y / 2, ny)
XI, YI = np.meshgrid(xi, yi)

height_mean = thick_sample
times = np.arange(0, T_max + 1, save_interval) * 0.4

# Bottom surface (flat)
Z_bot = np.zeros_like(XI)

# Initial noisy top surface
np.random.seed(42)
Z_top0 = height_mean + (np.random.rand(ny, nx) * 2 - 1) * noise_amp

rate = 0.04156

# Perimeter boundary indices (row-major order)
boundary = []
boundary += [0 * nx + i for i in range(nx)]
boundary += [j * nx + (nx - 1) for j in range(1, ny)]
boundary += [(ny - 1) * nx + i for i in range(nx - 2, -1, -1)]
boundary += [j * nx + 0 for j in range(ny - 2, 0, -1)]
pts_top0 = np.c_[XI.ravel(), YI.ravel(), Z_top0.ravel()]
boundary_coords = pts_top0[boundary]


def side_wall_coords(Z_top, xi, yi, Z_bot, ny):
    """
    Compute (X, Y, Z) surface coordinate pairs for the four side walls
    of the sample, used to render the 3-D surface plot.
    """
    coords = []
    # front (j=0)
    Xf = np.stack([xi, xi], axis=1)
    Yf = np.full_like(Xf, yi[0])
    Zf = np.stack([Z_bot[0, :], Z_top[0, :]], axis=1)
    coords.append((Xf, Yf, Zf))
    # back (j=ny-1)
    Xb = np.stack([xi, xi], axis=1)
    Yb = np.full_like(Xb, yi[-1])
    Zb = np.stack([Z_bot[-1, :], Z_top[-1, :]], axis=1)
    coords.append((Xb, Yb, Zb))
    # left (i=0)
    Xl = np.full((ny, 2), xi[0])
    Yl = np.stack([yi, yi], axis=1)
    Zl = np.stack([Z_bot[:, 0], Z_top[:, 0]], axis=1)
    coords.append((Xl, Yl, Zl))
    # right (i=nx-1)
    Xr = np.full((ny, 2), xi[-1])
    Yr = np.stack([yi, yi], axis=1)
    Zr = np.stack([Z_bot[:, -1], Z_top[:, -1]], axis=1)
    coords.append((Xr, Yr, Zr))
    return coords


# ── REACTOR GEOMETRY (for plasma cross-section polygon) ─────────────────────────
# Aliases for backward compatibility with code that uses the old names
R_chamber = R_CHAMBER
H_chamber = H_CHAMBER
R_wafer = R_WAFER
H_wafer = H_WAFER

points = [
    [0, H_wafer],
    [0, H_chamber],
    [R_wafer - 0.005, H_chamber],
    [R_wafer, H_chamber],
    [R_wafer, 2 * H_wafer],
    [R_chamber - (R_chamber - R_wafer) * 9 / 25,
     2 * H_wafer + (R_chamber - R_wafer) * 12 / 25],
    [R_chamber, 2 * H_wafer],
    [R_chamber, H_wafer * 3 / 4],
    [R_wafer, 0],
    [R_wafer, H_wafer],
    [R_wafer - 0.005, H_wafer],
    [0, H_wafer],
]

poly = shapely.Polygon(points)

# Re-export display-name mappings for backward compatibility
var_to_name = VAR_TO_NAME
name_to_var = NAME_TO_VAR
