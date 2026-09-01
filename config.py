"""
Centralized configuration for the RIE Digital Twin Dashboard.

This module consolidates all hardcoded values (sample counts, slider ranges,
model definitions, colors, database paths, etc.) so that extending the
dashboard—adding a new sample, probe, slider, or model stage—requires edits
in only one place.
"""

# ─── SAMPLE CONFIGURATION ───────────────────────────────────────────────────────
# Number of physical samples placed on the wafer platform.
# Changing this value propagates to parameter lists, UI controls, DB columns,
# initial rectangle positions, and color assignments.
NUM_SAMPLES = 4

SAMPLE_COLORS = {
    '1': '#cb181d',
    '2': '#fb6a4a',
    '3': '#fcae91',
    '4': '#fee5d9',
}

# Default positions and dimensions for each sample rectangle (meters).
INITIAL_RECTS = {
    '1': {'x': 0,     'y': 0,     'width': 0.01, 'height': 0.01, 'angle': 0.0},
    '2': {'x': 0,     'y': 0.045, 'width': 0.01, 'height': 0.01, 'angle': 0.0},
    '3': {'x': 0,     'y': 0.09,  'width': 0.01, 'height': 0.01, 'angle': 0.0},
    '4': {'x': 0.09,  'y': 0,     'width': 0.01, 'height': 0.01, 'angle': 0.0},
}


# ─── PROBE CONFIGURATION ────────────────────────────────────────────────────────
NUM_PROBES = 6
PROBE_ITEMS = [{"label": f"Probe {i + 1}"} for i in range(NUM_PROBES)]
DEFAULT_SELECTED_PROBES = [0, 3]

# Colors used in the probe selection UI
PROBE_SELECTED_COLOR = "#ff7f00"
PROBE_UNSELECTED_COLOR = "#e9ecef"


# ─── PARAMETER LISTS ────────────────────────────────────────────────────────────
# These lists define which parameters belong to each model stage.
# Location/size params are auto-generated from NUM_SAMPLES so adding a sample
# automatically creates the corresponding Loc_x / Loc_y entries.

LOCATION_PARAMS = (
    [f'Loc_x{i}' for i in range(1, NUM_SAMPLES + 1)]
    + [f'Loc_y{i}' for i in range(1, NUM_SAMPLES + 1)]
)
SIZE_PARAMS = ['Size_x', 'Size_y']
SAMPLE_GEOMETRY_PARAMS = SIZE_PARAMS + LOCATION_PARAMS

PLASMA_PARAMS = ['temperature', 'pressure', 'power', 'frequency']
FLOW_PARAMS = PLASMA_PARAMS + ['c_Cl_in', 'mass_flow'] + SAMPLE_GEOMETRY_PARAMS
ETCH_PARAMS = FLOW_PARAMS + ['flux_in', 'etch_time']
PERFORMANCE_PARAMS = ETCH_PARAMS + ['surface_height', 'probes']

PARAM_SETS = {
    'plasma': PLASMA_PARAMS,
    'flow': FLOW_PARAMS,
    'etch': ETCH_PARAMS,
    'performance': PERFORMANCE_PARAMS,
}

# Flat list of all storable parameters (used for DB inserts).
ALL_PARAMS = [
    'temperature', 'pressure', 'power', 'frequency',
    'mass_flow', 'c_Cl_in', 'flux_in', 'etch_time',
    'surface_height', 'probes',
] + SAMPLE_GEOMETRY_PARAMS


# ─── MODEL PIPELINE CONFIGURATION ───────────────────────────────────────────────
# Ordered list of model stages in the execution pipeline.
MODEL_ORDER = ['plasma', 'flow', 'etch', 'performance']

MODEL_CONFIG = {
    'plasma': {
        'title': 'Plasma Generation Model',
        'color': '#0057e7',
        'file': 'models/Plasma_Generation.mph',
        'type': 'comsol',          # 'comsol' = loaded via mph, 'script' = subprocess
    },
    'flow': {
        'title': 'Global Flow & Transport Model',
        'color': '#db4437',
        'file': 'models/Flow_202601.mph',
        'type': 'comsol',
    },
    'etch': {
        'title': 'Local Etching Model',
        'color': '#0f9d58',
        'file': 'models.Local_Etching_202601_dash',
        'type': 'script',
    },
    'performance': {
        'title': 'Performance Model',
        'color': '#ff7f00',
        'file': 'models/r1_faster.py',
        'type': 'script',
    },
}


# ─── SLIDER DEFINITIONS ─────────────────────────────────────────────────────────
# Each entry drives the creation of a slider in the "Edit Parameters" section.
# To add a new parameter slider, append a dict here; no layout code changes needed.
PRIMARY_SLIDERS = [
    {
        'id': 'temperature-slider',
        'label': 'Temperature (K)',
        'param_key': 'temperature',
        'value': 300, 'min': 250, 'max': 350, 'step': 1,
        'icon': 'fluent:temperature-16-filled',
        'color': None,   # None → default Mantine blue
    },
    {
        'id': 'pressure-slider',
        'label': 'Pressure (torr)',
        'param_key': 'pressure',
        'value': 0.1, 'min': 0.05, 'max': 0.2, 'step': 0.001,
        'icon': 'lets-icons:pressure',
        'color': None,
    },
    {
        'id': 'power-slider',
        'label': 'Power (W)',
        'param_key': 'power',
        'value': 75, 'min': 5, 'max': 150, 'step': 0.1,
        'icon': 'icomoon-free:power',
        'color': None,
    },
    {
        'id': 'frequency-slider',
        'label': 'Frequency (MHz)',
        'param_key': 'frequency',
        'value': 13.56, 'min': 10.00, 'max': 15.00, 'step': 0.01,
        'icon': 'mdi:sine-wave',
        'color': None,
    },
    {
        'id': 'mass-flow-slider',
        'label': 'Mass Flow (kg/s x10^-6)',
        'param_key': 'mass_flow',
        'value': 4.6, 'min': 0, 'max': 10, 'step': 0.01,
        'icon': 'streamline:wind-flow-2-solid',
        'color': '#db4437',
    },
    {
        'id': 'etch-time-slider',
        'label': 'Etch Time (s)',
        'param_key': 'etch_time',
        'value': 500, 'min': 200, 'max': 800, 'step': 1.0,
        'icon': 'mingcute:time-fill',
        'color': '#0f9d58',
    },
]

DERIVED_SLIDERS = [
    {
        'id': 'cl-inlet-slider',
        'label': 'Inlet Cl Concentration (mol/m^3)',
        'param_key': 'c_Cl_in',
        'value': 1e-4, 'min': 0, 'max': 1e-3, 'step': 1e-6,
        'icon': 'gravity-ui:dots-9',
        'color': '#db4437',
    },
    {
        'id': 'flux-in-slider',
        'label': 'Total Flux (mol/m^2/s x10^-6)',
        'param_key': 'flux_in',
        'value': 25, 'min': 0.1, 'max': 100, 'step': 0.1,
        'icon': 'gravity-ui:dots-9',
        'color': '#0f9d58',
    },
    {
        'id': 'surface-height-slider',
        'label': 'Mean Surface Height',
        'param_key': 'surface_height',
        'value': 50, 'min': 0.0, 'max': 100, 'step': 0.1,
        'icon': 'gravity-ui:dots-9',
        'color': '#0f9d58',
    },
]


# ─── DATABASE CONFIGURATION ─────────────────────────────────────────────────────
DB_PATH = 'database/rie_twin.db'

# Columns shown in the "Load Previously Run Parameters" AG Grid table.
DATABASE_DISPLAY_COLUMNS = (
    ['status', 'roughness', 'nonuniformity', 'effective_conductivity', 'conductance']
    + ['temperature', 'pressure', 'power', 'frequency', 'mass_flow', 'etch_time', 'probes']
    + SAMPLE_GEOMETRY_PARAMS
)
DATABASE_DROP_DUPLICATE_COLS = DATABASE_DISPLAY_COLUMNS[5:]


# ─── VARIABLE DISPLAY NAMES ─────────────────────────────────────────────────────
# Mapping from internal variable keys to human-readable labels used in dropdowns
# and plot axes.
VAR_TO_NAME = {
    'c_Cl':        'Cl Concentration (mol/m3)',
    'flux':        'Wafer Surface Cl Flux (mol/m2/s)',
    'v':           'Cl Velocity Magnitude (m/s)',
    'n_wCl':       'Cl Number Density (1/m3)',
    'n_wCl_1p':    'Cl+ Number Density (1/m3)',
    'n_wCl2_1p':   'Cl2+ Number Density (1/m3)',
    'Viz_wCl_1p':  'Cl+ ion z-component velocity (m/s)',
    'Viz_wCl2_1p': 'Cl2+ ion z-component velocity (m/s)',
    'V':           'Voltage',
    'R':           'Resistance',
    'I':           'Terminal Current',
    'V0':          'Terminal Voltage',
    'conductivity': 'Conductivity',
}

NAME_TO_VAR = {v: k for k, v in VAR_TO_NAME.items()}


# ─── GEOMETRY CONSTANTS (reactor / wafer) ────────────────────────────────────────
R_CHAMBER = 0.17688
H_CHAMBER = 0.2
R_WAFER = 0.1
H_WAFER = 0.075

# Height offset used to locate the wafer surface plane in flow results.
SURFACE_Z = H_WAFER + 0.001

# ─── PHYSICAL CONSTANTS ─────────────────────────────────────────────────────────
AVOGADRO = 6.022e23


# ─── DASHBOARD APPEARANCE ───────────────────────────────────────────────────────
APP_TITLE = "Digital Twin of RIE"
HEADER_STYLE = {'color': '#333', 'fontFamily': 'Arial, sans-serif'}
SECTION_FONT = {'fontFamily': 'Arial, sans-serif'}
