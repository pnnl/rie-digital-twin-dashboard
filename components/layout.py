"""
Dashboard layout components for the RIE Digital Twin.

All hardcoded values (slider ranges, sample counts, model colors, etc.) are
sourced from ``config.py`` so that extending the dashboard requires changes in
only one place.
"""

from dash import dcc, html
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
from dash_iconify import DashIconify
import dash_daq as daq
import dash_ag_grid as dag

import utils.database_utils as db
from config import (
    APP_TITLE, HEADER_STYLE,
    NUM_SAMPLES, SAMPLE_COLORS, INITIAL_RECTS,
    NUM_PROBES, PROBE_ITEMS, DEFAULT_SELECTED_PROBES,
    PROBE_SELECTED_COLOR, PROBE_UNSELECTED_COLOR,
    PRIMARY_SLIDERS, DERIVED_SLIDERS,
    MODEL_CONFIG, MODEL_ORDER,
    DATABASE_DISPLAY_COLUMNS,
    DATABASE_DROP_DUPLICATE_COLS
)


# ─── DATA LOADING ───────────────────────────────────────────────────────────────
# Load and de-duplicate the parameters table once at import time so the AG Grid
# is populated on first render.

database_df = db.load_data('parameters')
database_df = database_df[DATABASE_DISPLAY_COLUMNS].drop_duplicates(
    subset=DATABASE_DROP_DUPLICATE_COLS, keep='last'
)
database_df = db.drop_redundant_nans(database_df)


# ─── REUSABLE COMPONENT BUILDERS ────────────────────────────────────────────────

def _build_slider(slider_cfg, span=3):
    """
    Build a single Mantine slider wrapped in a GridCol from a config dict.

    Parameters
    ----------
    slider_cfg : dict
        Must contain keys: id, label, value, min, max, step, icon.
        Optional: color (defaults to Mantine blue).
    span : int
        Grid column span.
    """
    slider_kwargs = dict(
        id=slider_cfg['id'],
        value=slider_cfg['value'],
        min=slider_cfg['min'],
        max=slider_cfg['max'],
        step=slider_cfg['step'],
        labelAlwaysOn=True,
        thumbChildren=DashIconify(icon=slider_cfg['icon'], width=32),
        thumbSize=26,
        style={"thumb": {"borderWidth": 2, "padding": 3}},
    )
    if slider_cfg.get('color'):
        slider_kwargs['color'] = slider_cfg['color']

    return dmc.GridCol(
        dmc.Stack(
            [dmc.Text(slider_cfg['label'], size="sm"),
             dmc.Slider(**slider_kwargs)],
            style={'width': '100%'},
        ),
        span=span,
    )


def _build_progress_card(model_id):
    """
    Return the standard progress-bar card used by every model section.

    Each model stage uses the same three sub-components (stage text,
    animated progress bar, percentage label) differentiated only by
    ``model_id`` which is used to construct unique Dash component IDs.
    """
    return dbc.Card([
        dbc.CardBody([
            html.H4("Current Progress", className="card-title"),
            html.Div(id=f"{model_id}-current-stage", className="mb-2"),
            dbc.Progress(
                id=f"{model_id}-progress-bar",
                value=0,
                striped=True,
                animated=True,
                color="info",
                style={"height": "30px"},
            ),
            html.Div(id=f"{model_id}-progress-text", className="mt-2 text-center"),
        ])
    ], className="mb-4")


def _graph_panel(graph_id, color, height='30vw', extra_config=None):
    """Wrap a dcc.Graph in a styled container panel."""
    config = extra_config or {}
    force_full_height = False
    if 'force_full_height' in config:
        force_full_height = config.pop('force_full_height')
    graph_style = {'height': height}
    outer_style = {
        'border': f'1px solid {color}',
        'borderRadius': '5px',
        'padding': '10px',
        'background': '#f0f8ff',
    }
    if force_full_height:
        graph_style = {'height': '100%', 'minHeight': '0'}
        outer_style.update({'height': '100%', 'minHeight': '0', 'display': 'flex', 'flexDirection': 'column'})
    return html.Div(
        [dcc.Graph(id=graph_id, style=graph_style, config=config)],
        style=outer_style,
    )


# ─── SECTION BUILDERS ───────────────────────────────────────────────────────────

def create_collapsible_div(div_id, title, content,
                           is_open=True, color="#363636", width='98%',
                           information=None):
    """
    Wrap *content* in a collapsible Bootstrap card with an info popover.

    The collapse is driven by pattern-matching callbacks keyed on *div_id*.
    """
    if information is None:
        information = ["This is a detailed explanation.", html.Br(),
                       html.A("Learn more", href="#")]

    return dbc.Card([
        dbc.CardHeader([
            html.Div([
                html.H5(
                    [title + ' ',
                     html.Span("ⓘ", id=f"popover-target-{div_id}",
                               style={"cursor": "pointer", "color": color,
                                      "fontSize": "18px"})],
                    className="mb-0",
                    style={'color': color, 'fontFamily': 'Arial, sans-serif'},
                ),
                dbc.Popover(
                    [dbc.PopoverHeader("Information"),
                     dbc.PopoverBody(information)],
                    target=f"popover-target-{div_id}",
                    trigger="hover",
                    placement="right",
                ),
                dbc.Button(
                    "▼" if is_open else "▶",
                    id={"type": "collapse-button", "index": div_id},
                    className="btn btn-link p-0",
                    style={"border": "none", "background": "none",
                           "font-size": "1.2em", "color": "#007bff"},
                    n_clicks=0,
                ),
            ], style={"display": "flex", "justify-content": "space-between",
                      "align-items": "center"}),
        ]),
        dbc.Collapse(
            dbc.CardBody(content),
            id={"type": "collapse", "index": div_id},
            is_open=is_open,
        ),
    ], style={"margin": "20px", 'border': f'2px solid {color}', 'width': width})


def create_header():
    """Render the top-level dashboard title."""
    return html.Div(
        [html.H1(APP_TITLE, style=HEADER_STYLE)],
        style={'textAlign': 'center', 'marginBottom': '30px'},
    )


# ─── DATABASE / PARAMETER LOADING TABLE ─────────────────────────────────────────

def create_parameters_load():
    """
    Build an AG Grid showing previously-run parameter combinations.

    The status column is color-coded (red/blue/green) via a JS cell-style
    function.  Large numeric columns are formatted without decimals.
    """
    status_col = {
        "headerName": "status",
        "field": "status",
        "cellStyle": {
            "function": (
                "params.value && params.value.includes('Failed') ? {'color': 'red'} : "
                "params.value && params.value.includes('Started') ? {'color': 'blue'} : "
                "params.value && params.value.includes('Completed') ? {'color': 'green'} : {}"
            )
        },
    }
    no_decimal_cols = {'effective_conductivity', 'conductance'}
    column_defs = [
        status_col if col == "status"
        else {"headerName": col, "field": col,
              "valueFormatter": {"function": "d3.format(',.0f')(params.value)"}}
        if col in no_decimal_cols
        else {"headerName": col, "field": col}
        for col in database_df.columns
    ]

    return html.Div([dag.AgGrid(
        id="database-table",
        columnDefs=column_defs,
        rowData=database_df.to_dict("records"),
        dashGridOptions={
            'rowSelection': {'mode': 'singleRow', 'enableClickSelection': True},
        },
        defaultColDef={"sortable": True, "filter": True},
    )])


# ─── PRIMARY PARAMETER SLIDERS ──────────────────────────────────────────────────

def create_parameters_input():
    """
    Build the "Edit Parameters and Submit Runs" section.

    Sliders are generated from ``PRIMARY_SLIDERS`` in config.py, so adding a
    new controllable parameter only requires appending to that list.
    """
    # Build sliders from config
    slider_grid_cols = [_build_slider(s) for s in PRIMARY_SLIDERS]

    return html.Div([
        # Slider grid
        html.Div([
            html.Div([
                dmc.Grid(slider_grid_cols, style={'width': '100%'}, gutter='xl'),
            ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '10px'}),
        ]),

        # Sample position editor + probe selector
        html.Div([
            dmc.Grid([
                dmc.GridCol(create_sample_input(), span=8,
                            style={"display": "flex", "flexDirection": "column"}),
                dmc.GridCol(create_probe_input(), span=4,
                            style={"display": "flex", "flexDirection": "column"}),
            ], style={'width': '100%'}, gutter='xl',
               justify='space-between', align="stretch"),
        ], style={'display': 'flex', 'gap': '10px', 'marginBottom': '10px'}),

        # Submit button
        html.Div([
            dmc.Button(
                [DashIconify(icon="mdi:play-circle", width=20),
                 " Submit These Parameters"],
                id='update-all', n_clicks=0,
                size="lg", radius="md", variant="filled", color="blue",
                fullWidth=False,
                style={"width": "30%", "marginTop": "20px"},
            ),
        ], style={"display": "flex", "justifyContent": "center",
                  "marginTop": "10px"}),
    ], style={'marginBottom': '20px', 'borderRadius': '10px',
              'padding': '10px', 'marginRight': '0%', 'background': '#ffffff'})


# ─── SAMPLE POSITION EDITOR ─────────────────────────────────────────────────────

def create_sample_input():
    """
    Interactive panel for positioning rectangular samples on the wafer.

    Sample IDs, default positions, and colors are driven by
    ``INITIAL_RECTS``, ``SAMPLE_COLORS``, and ``NUM_SAMPLES`` in config.py.
    """
    sample_ids = [str(i) for i in range(1, NUM_SAMPLES + 1)]
    input_style = {'width': '80px'}

    div = html.Div([
        # Control panel for width/height/position
        html.Div([
            html.H4("Sample Parameters"),

            # Width input
            html.Div([
                html.Label("Width:"),
                dcc.Input(id='width-input', type='number',
                          value=INITIAL_RECTS['1']['width'],
                          step=0.0001, style=input_style),
            ], style={'display': 'flex', 'margin': '10px',
                      'align-items': 'center', 'justify-content': 'space-between'}),

            # Height input
            html.Div([
                html.Label("Height:"),
                dcc.Input(id='height-input', type='number',
                          value=INITIAL_RECTS['1']['height'],
                          step=0.0001, style=input_style),
            ], style={'display': 'flex', 'margin': '10px',
                      'align-items': 'center', 'justify-content': 'space-between'}),
            html.Br(),

            # Sample selector dropdown with color indicator
            html.Label("Sample Number:"),
            html.Div([
                html.Div(id='output-color-box',
                         style={'width': '25px', 'height': '25px',
                                'border': '1px solid black',
                                'backgroundColor': SAMPLE_COLORS['1'],
                                'margin': '5px', 'display': 'flex'}),
                dcc.Dropdown(id='rect-id', options=sample_ids, value='1'),
            ], style={'display': 'flex', 'margin': '10px'}),

            # X center
            html.Div([
                html.Label("X(center):"),
                dcc.Input(id='x-center', type='number',
                          value=INITIAL_RECTS['1']['x'],
                          step=0.001, style=input_style),
            ], style={'display': 'flex', 'margin': '10px',
                      'align-items': 'center', 'justify-content': 'space-between'}),

            # Y center
            html.Div([
                html.Label("Y(center):"),
                dcc.Input(id='y-center', type='number',
                          value=INITIAL_RECTS['1']['y'],
                          step=0.001, style=input_style),
            ], style={'display': 'flex', 'margin': '10px',
                      'align-items': 'center', 'justify-content': 'space-between'}),
        ], style={'width': '50%', 'padding': '20px',
                  'borderRadius': '8px', 'margin': '10px'}),

        # Interactive wafer plot
        dcc.Graph(id='interactive-plot',
                  config={'editable': True, 'displayModeBar': True},
                  style={'height': '100%', 'width': '100%'}),

        # Hidden store holding current rectangle geometries
        dcc.Store(id="rect-data", data=INITIAL_RECTS),
    ], style={
        'display': 'flex', 'flexDirection': 'row',
        'alignItems': 'center', 'justifyContent': 'center',
        'padding': '10px', 'backgroundColor': '#f9f9f9',
        'border': f'2px solid {MODEL_CONFIG["flow"]["color"]}',
        'borderRadius': '8px',
        'boxShadow': '0 2px 4px rgba(0,0,0,0.1)',
        'flex': '1', 'margin': '0px',
    })
    return div


# ─── PROBE SELECTOR ─────────────────────────────────────────────────────────────

def create_probe_input():
    """
    Clickable probe-card selector arranged around a sample square.

    The number of probes is set by ``NUM_PROBES`` in config.py; cards are
    split evenly into left and right columns.
    """
    return html.Div([
        html.Header(html.H4("Select Probes:"),
                    style={'padding': '10px', 'textAlign': 'center'}),

        # Container: probe cards overlaid on sample square
        html.Div([
            html.Div(id="cards-container",
                     style={"display": "flex", "justifyContent": "center",
                            "position": "absolute", "top": "0", "left": "50%",
                            "transform": "translateX(-50%)", "zIndex": "2"}),
            html.Div("Sample", className="base-square",
                     style={"width": "300px", "height": "300px",
                            "display": "flex", "background": "#b8bec8",
                            "alignItems": "center", "justifyContent": "flex-end",
                            'flexDirection': "column", 'marginBottom': '7px',
                            "position": "relative"}),
        ], style={"position": "relative", "width": "fit-content",
                  "margin": "0 auto"}),

        html.Header(
            html.H4(f"Please select two of the {NUM_PROBES} available probes "
                     "for performance modeling."),
            style={'padding': '10px', 'textAlign': 'center'}),
    ], style={
        'alignItems': 'center', 'justifyContent': 'center',
        'padding': '10px', 'backgroundColor': '#f9f9f9',
        'border': f'2px solid {PROBE_SELECTED_COLOR}',
        'borderRadius': '8px',
        'boxShadow': '0 2px 4px rgba(0,0,0,0.1)',
        'flex': '1', 'margin': '0px',
    })


# ─── DERIVED PARAMETER SLIDERS ──────────────────────────────────────────────────

def create_derived_parameters_input():
    """
    Sliders for model-computed intermediate parameters (Cl concentration,
    flux, surface height).  Generated from ``DERIVED_SLIDERS`` in config.py.
    """
    slider_stacks = []
    for s in DERIVED_SLIDERS:
        kwargs = dict(
            id=s['id'], value=s['value'], min=s['min'], max=s['max'],
            step=s['step'], labelAlwaysOn=True,
            thumbChildren=DashIconify(icon=s['icon'], width=32),
            thumbSize=26, style={"thumb": {"borderWidth": 2, "padding": 3}},
        )
        if s.get('color'):
            kwargs['color'] = s['color']
        slider_stacks.append(
            dmc.Stack([dmc.Text(s['label'], size="sm"),
                       dmc.Slider(**kwargs)],
                      style={'width': '25%'})
        )

    return html.Div([
        html.Div([
            html.P("DO NOT MANUALLY ADJUST THESE PARAMETERS. "
                   "They are computed as outputs from the models."),
            html.Div(slider_stacks,
                     style={'display': 'flex', 'gap': '10px',
                            'marginBottom': '10px'}),
        ]),
    ], style={'marginBottom': '20px', 'borderRadius': '10px',
              'padding': '10px', 'marginRight': '0%', 'background': '#ffffff'})


# ─── MODEL OUTPUT BOXES ─────────────────────────────────────────────────────────

def create_model_box(model_id, title, color, width='48%'):
    """
    Standard model output box with progress card, variable dropdown, and
    a single output graph.  Used for the Plasma and Flow model sections.
    """
    return html.Div([
        _build_progress_card(model_id),
        # Variable selector + output plot
        html.Div([
            dcc.Dropdown(id=f'{model_id}-variable-select', options=[]),
            dcc.Graph(id=f'{model_id}-output-graph', style={'height': '30vw'}),
        ], style={'border': f'1px solid {color}', 'borderRadius': '5px',
                  'padding': '10px', 'background': '#f0f8ff'}),
    ], style={'width': width, 'background': '#ffffff'})


def create_etch_box(model_id, title, color, width='48%'):
    """
    Etching model output box with two side-by-side graphs (etch-rate grid
    and animated surface evolution) plus playback controls.

    The sample selector dropdown options are generated from ``NUM_SAMPLES``.
    """
    sample_ids = [str(i) for i in range(1, NUM_SAMPLES + 1)]

    return html.Div([
        dbc.Row([dbc.Col([_build_progress_card(model_id)])]),

        dbc.Row([
            # Left graph: etch rate overview
            dbc.Col(
                html.Div(
                    _graph_panel(
                        f'{model_id}-output-graph-1', color, height='100%',
                        extra_config={
                            'scrollZoom': True,
                            'doubleClick': 'reset+autosize',
                            'displayModeBar': True,
                            'staticPlot': False,
                            'force_full_height': True
                        }
                    ),
                    style={
                        'height': '100%',
                        'minHeight': '39vw',
                        'display': 'flex',
                        'flexDirection': 'column',
                        'justifyContent': 'stretch',
                    }
                ),
                width=6, className="h-100",
                style={'height': '100%', 'minHeight': '39vw'}
            ),
            dbc.Col(
                html.Div([
                    dbc.Row([
                        dbc.Col([
                            html.Label("Sample Number:"),
                            dcc.Dropdown(id=f'{model_id}-sample-selector',
                                         options=sample_ids, value='1')
                        ],
                            style={
                                'display': 'flex',
                                'flexDirection': 'column',
                                'justifyContent': 'center',
                                'alignItems': 'center',
                                'marginBottom': '10px'
                            }
                        ),
                        dbc.Col(
                            daq.LEDDisplay(
                                id='roughness-display',
                                label='Roughness (nm)',
                                value=0,
                                color=color
                            ),
                            style={'display': 'flex', 'justifyContent': 'center', 'marginBottom': '10px'}
                        ),
                        dbc.Col(
                            daq.LEDDisplay(
                                id='nonuniformity-display',
                                label='Nonuniformity (nm)',
                                value=0,
                                color=color
                            ),
                            style={'display': 'flex', 'justifyContent': 'center', 'marginBottom': '10px'}
                        ),
                    ]),
                    dcc.Graph(id=f'{model_id}-output-graph-2',
                              style={'flex': '1 1 auto', 'height': '100%', 'minHeight': '0'}),
                    html.Div([
                        html.Button("Play", id="play-button", n_clicks=0),
                        html.Button("Pause", id="pause-button", n_clicks=0),
                        html.Button("Reset", id="reset-button", n_clicks=0),
                        html.Div("Frame: 0", id="frame-display",
                                 style={'margin': '10px'}),
                    ], style={'text-align': 'center', 'margin': '5px'}),
                ], style={
                    'border': f'1px solid {color}',
                    'borderRadius': '10px',
                    'padding': '10px',
                    'background': '#f0f8ff',
                    'height': '100%',
                    'minHeight': '39vw',
                    'display': 'flex',
                    'flexDirection': 'column',
                }),
                className="h-100",
                style={'height': '100%', 'minHeight': '39vw'}
            ),
        ], style={"display": "flex", "alignItems": "stretch", 'height': '39vw', 'minHeight': '39vw'}),
    ], style={'width': width, 'borderRadius': '10px', 'padding': '10px',
              'marginRight': '2%', 'background': '#ffffff'})


def create_performance_box(model_id, title, color, width='48%'):
    """
    Performance model output box with LED displays for scalar metrics
    (conductance, conductivity) and two output graphs.
    """
    return html.Div([
        dbc.Row([dbc.Col([_build_progress_card(model_id)])]),

        # Scalar LED displays
        dbc.Row([
            dbc.Col(
                daq.LEDDisplay(id='effective-conductivity-display',
                               label='Effective Conductivity (S/m)',
                               value=0, color=color),
                width=6,
                style={'display': 'flex', 'justifyContent': 'center',
                       'marginBottom': '10px'}),
            dbc.Col(
                daq.LEDDisplay(id='conductance-display',
                               label='Conductance (S)',
                               value=0, color=color),
                style={'display': 'flex', 'justifyContent': 'center',
                       'marginBottom': '10px'}),
        ]),

        # Two side-by-side output graphs
        dbc.Row([
            dbc.Col(_graph_panel(f'{model_id}-output-graph-1', color), width=6),
            dbc.Col(_graph_panel(f'{model_id}-output-graph-2', color)),
        ]),
    ], style={'width': width, 'borderRadius': '10px', 'padding': '10px',
              'marginRight': '2%', 'background': '#ffffff'})


# ─── MODEL INFORMATION TOOLTIPS ──────────────────────────────────────────────────

# Centralised help text for each model section's info popover.
_MODEL_INFO = {
    'plasma': [
        html.P("This COMSOL model simulates the plasma generation process "
               "in the RIE system. It takes as inputs the parameters related "
               "to plasma generation (power, pressure, etc.) and outputs "
               "plasma properties such as species concentrations and fluxes."),
        html.P("Key outputs from this model which are used as inputs to the "
               "other models include:"),
        html.Ul([
            html.Li("Inlet Cl Concentration: The concentration of chlorine "
                    "species at the entrance to the reactor."),
            html.Li("Radial Density Distribution: The number density "
                    "distribution of reactive species across the sample surface."),
            html.Li("Radial Velocity Distribution: The velocity distribution "
                    "of reactive species across the sample surface."),
        ]),
    ],
    'flow': [
        html.P("This COMSOL model simulates the flow and transport of "
               "reactive species within the reactor. It takes as inputs the "
               "plasma properties from the Plasma Generation Model and outputs "
               "the mass flux of Cl reaching the sample surface."),
    ],
    'etch': [
        html.P("This Python model simulates the local etching process in the "
               "RIE system. It takes as inputs the number densities from the "
               "Plasma Generation Model and the mass flux of Cl from the "
               "Global Flow & Transport Model to compute the spatial "
               "dependence of the physical and chemical etching rates. It "
               "outputs the total combined etch rate and final height profile "
               "of the sample surface."),
    ],
    'performance': [
        html.P("This COMSOL model simulates the performance of the "
               "fabricated device. It takes as inputs the final sample height "
               "profile from the Local Etching Model and outputs device "
               "performance metrics such as conductance, conductivity, "
               "and voltage."),
    ],
}

# Maps model key → function that builds its output box.
_MODEL_BOX_BUILDERS = {
    'plasma':      lambda c: create_model_box('plasma', c['title'], c['color'], width='100%'),
    'flow':        lambda c: create_model_box('flow', c['title'], c['color'], width='100%'),
    'etch':        lambda c: create_etch_box('etch', c['title'], c['color'], width='100%'),
    'performance': lambda c: create_performance_box('performance', c['title'], c['color'], width='100%'),
}


# ─── MAIN LAYOUT ────────────────────────────────────────────────────────────────

def create_layout():
    """
    Assemble the full dashboard layout.

    The page is structured as:
      1. Header
      2. System details (collapsible)
      3. Load previously run parameters (collapsible)
      4. Edit parameters & submit (collapsible)
      5. Derived parameters (collapsible)
      6. Model pipeline sections with directional arrows
      7. Hidden interval timers and data stores
    """
    # --- Build colour-legend for the parameter info popover ---
    color_legend = [
        html.P("Modify parameters below and hit submit to run the digital "
               "twin with the selected parameters. Parameter controls are "
               "color coded by the model which they affect: "),
    ]
    for model_key in MODEL_ORDER:
        cfg = MODEL_CONFIG[model_key]
        color_legend.append(html.P([
            html.Span(cfg['title'].split()[0],
                      style={"color": cfg['color'], "fontWeight": "bold"}),
            f" = parameters which affect the {cfg['title']}.",
        ]))

    # --- Build model pipeline visual ---
    plasma_cfg = MODEL_CONFIG['plasma']
    flow_cfg = MODEL_CONFIG['flow']
    etch_cfg = MODEL_CONFIG['etch']
    perf_cfg = MODEL_CONFIG['performance']

    model_pipeline = [
        # Row 1: Plasma → Flow
        html.Div([
            create_collapsible_div(
                "plasma-collapse", plasma_cfg['title'],
                _MODEL_BOX_BUILDERS['plasma'](plasma_cfg),
                True, plasma_cfg['color'], width='48%',
                information=_MODEL_INFO['plasma']),
            # Horizontal arrow
            html.Div([
                html.Div("→", style={'fontSize': '48px', 'color': plasma_cfg['color'],
                                     'fontWeight': 'bold', 'textAlign': 'left'}),
            ], style={'width': '4%', 'display': 'flex', 'alignItems': 'center',
                      'justifyContent': 'left'}),
            create_collapsible_div(
                "flow-collapse", flow_cfg['title'],
                _MODEL_BOX_BUILDERS['flow'](flow_cfg),
                True, flow_cfg['color'], width='48%',
                information=_MODEL_INFO['flow']),
        ], style={'display': 'flex', 'justifyContent': 'space-between',
                  'marginBottom': '20px'}),

        # Down arrows from Plasma & Flow
        html.Div([
            html.Div("↓", style={'fontSize': '48px', 'color': plasma_cfg['color'],
                                 'fontWeight': 'bold', 'textAlign': 'center', 'width': '48%'}),
            html.Div("↓", style={'fontSize': '48px', 'color': flow_cfg['color'],
                                 'fontWeight': 'bold', 'textAlign': 'center', 'width': '48%'}),
        ], style={'display': 'flex', 'alignItems': 'center',
                  'justifyContent': 'center', 'marginBottom': '20px'}),

        # Row 2: Etch (full width)
        html.Div([
            create_collapsible_div(
                "etch-collapse", etch_cfg['title'],
                _MODEL_BOX_BUILDERS['etch'](etch_cfg),
                True, etch_cfg['color'],
                information=_MODEL_INFO['etch']),
        ], style={'marginTop': '20px', 'marginBottom': '20px'}),

        # Down arrow from Etch
        html.Div([
            html.Div("↓", style={'fontSize': '48px', 'color': etch_cfg['color'],
                                 'fontWeight': 'bold', 'textAlign': 'center', 'width': '100%'}),
        ], style={'display': 'flex', 'alignItems': 'center',
                  'justifyContent': 'center', 'marginBottom': '20px'}),

        # Row 3: Performance (full width)
        html.Div([
            create_collapsible_div(
                "performance-collapse", perf_cfg['title'],
                _MODEL_BOX_BUILDERS['performance'](perf_cfg),
                True, perf_cfg['color'],
                information=_MODEL_INFO['performance']),
        ], style={'marginTop': '20px', 'marginBottom': '100px'}),
    ]

    # --- Hidden stores and timers ---
    stores_and_timers = [
        # Periodic polling for model progress / results
        dcc.Interval(id='interval-component', interval=5000, n_intervals=0),
        # Faster interval for etching animation frames
        dcc.Interval(id='frame-interval-component', interval=500, n_intervals=0),

        # UI state stores
        dcc.Store(id="selected-probes", data=DEFAULT_SELECTED_PROBES),
        dcc.Store(id="parameter-values", data={}),
        dcc.Store(id="running-model", data={}),
        dcc.Store(id="etch-time-data", data=15.0),

        # Per-model result counters (trigger downstream callbacks on change)
        *[dcc.Store(id=f"{m}-data", data={'count': 0}) for m in MODEL_ORDER],

        # Active database row and background task tracking
        dcc.Store(id="database-row", data={}),
        dcc.Store(id="current-task-store", data={"task_id": None}),

        # Etching animation state
        dcc.Store(id='is-playing', data=True),
        dcc.Store(id='current-frame', data=0),
        dcc.Store(id='z-top-store', data=None),
        dcc.Store(id='t-store', data=None),
        dcc.Store(id='rate-store', data=0),
    ]

    # --- Assemble full page ---
    layout = dmc.MantineProvider(
        html.Div([
            create_header(),

            create_collapsible_div(
                "details", "System Details",
                html.Div(html.Ul([
                    html.Li("Metal material: Aluminum"),
                    html.Li("Gas: Cl2+BCl3, 86 sccm and 3 sccm"),
                ])),
                True,
                information=[html.P("Details regarding the system being modeled. "
                                    "This includes the material being etched and "
                                    "the gas composition.")]),

            create_collapsible_div(
                "load", "Load Previously Run Parameters",
                create_parameters_load(), False,
                information=[
                    "View parameter combinations which have already been run "
                    "and have results saved in the database. ",
                    "Select a parameter combination using the checkbox to "
                    "populate the parameter selections in the box below. ",
                    "If desired, you will be able to modify individual "
                    "parameter values below after loading a parameter set "
                    "before hitting submit. ",
                ]),

            create_collapsible_div(
                "input", "Edit Parameters and Submit Runs",
                create_parameters_input(), True,
                information=color_legend),

            create_collapsible_div(
                "dervied", "Derived Parameters",
                create_derived_parameters_input(), False,
                information=[html.P("These are intermediate parameters "
                                    "calculated by the models. Please DO NOT "
                                    "ADJUST THESE PARAMETERS MANUALLY.")]),

            *model_pipeline,
            *stores_and_timers,
        ])
    )

    return layout
