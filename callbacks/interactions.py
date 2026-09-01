"""
Dash callbacks for the RIE Digital Twin Dashboard.

This module registers all interactive callbacks that drive the dashboard:
parameter submission, model execution, progress polling, result storage,
and visualization.  Hardcoded constants are imported from ``config.py``.

File organisation
-----------------
The callbacks and helpers are grouped into logical sections:

1. **Background Processing Engine**
   ``BackgroundProcessor`` class and helpers for threaded COMSOL / script runs.

2. **Database & Parameter Management**
   Loading / saving parameters, detecting which pipeline stage to re-run.

3. **Model Result Storage** (pipeline order: plasma → flow → etch → performance)
   Interval-polled callbacks that harvest results from the background processor,
   persist them to the database, and push derived values back to the UI.

4. **Progress Monitoring**
   Polling callback that updates the per-model progress bars.

5. **Result Visualization**
   Static result plots (cross-sections, heatmaps) and the 3-D etch animation.

6. **Sample Placement UI**
   Interactive wafer-platform plot where users drag sample rectangles.

7. **Probe Selection UI**
   Clickable probe cards for selecting measurement probes.

8. **UI Panel Controls**
   Collapsible section toggles.
"""


import dash
from dash import Input, Output, callback, State, ALL, ctx, callback_context, html
import dash_bootstrap_components as dbc

import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from scipy.interpolate import griddata

import time
import mph
import re
import os
import numpy as np
import threading
import pandas as pd
from datetime import datetime
import shapely

from functools import reduce

import subprocess

from utils.params import *
import utils.database_utils as db
import utils.surface_roughness as sr

from dash.exceptions import PreventUpdate

# Centralised configuration ── all hardcoded values live here
from config import (
    NUM_SAMPLES, SAMPLE_COLORS, INITIAL_RECTS,
    NUM_PROBES, PROBE_ITEMS, DEFAULT_SELECTED_PROBES,
    PROBE_SELECTED_COLOR, PROBE_UNSELECTED_COLOR,
    PARAM_SETS, MODEL_CONFIG, MODEL_ORDER,
    DATABASE_DISPLAY_COLUMNS,
    DATABASE_DROP_DUPLICATE_COLS,
    VAR_TO_NAME, NAME_TO_VAR,
    R_WAFER, H_WAFER, SURFACE_Z, AVOGADRO, DB_PATH,
)

# Global registry of running background tasks (keyed by task_id)
background_tasks = {}

# Start the COMSOL client once at module load
client = mph.start()


# Create logging and output directories if they don't exist
os.makedirs('logs', exist_ok=True)
os.makedirs('outputs', exist_ok=True)
os.makedirs('database', exist_ok=True)

# If database doesn't exist, create it and the parameters table
if not os.path.exists(DB_PATH):
    db.create_database(DB_PATH)


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND PROCESSING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def get_radius(x, y):
    """Euclidean distance from the origin (used for radial flux lookups)."""
    return np.sqrt(x**2 + y**2)

    

class BackgroundProcessor:
    """
    Manages a background COMSOL / script model run in a separate thread.

    The processor spawns a writer thread (runs the model) and a reader thread
    (parses the log file for progress updates).  Progress and status are
    exposed as attributes so the polling callback can read them.
    """

    # Default parameter values (used when kwargs are missing)
    _PARAM_DEFAULTS = {
        'temperature': 300, 'pressure': 0.1, 'power': 75,
        'frequency': 14.56, 'mass_flow': 4.6, 'c_Cl_in': 1e-4,
        'flux_in': 2.63, 'etch_time': 15.0, 'surface_height': 1,
        'probes': '1-4',
    }

    def __init__(self, task_id, run_model, row_id,
                 use_saved=False, **kwargs):
        self.task_id = task_id
        self.run_model = run_model
        self.row_id = row_id
        self.log_file = f"logs/computation_{task_id}.log"
        self.stop_event = threading.Event()
        self.status = "idle"
        self.current_progress = 0
        self.current_stage = "Not started"
        self.use_saved = use_saved
        self.duration = -1

        # Resolve model file from centralised config
        self.model_file = MODEL_CONFIG[self.run_model]['file']

        # Populate physical parameters from kwargs (fall back to defaults)
        for key, default in self._PARAM_DEFAULTS.items():
            setattr(self, key, kwargs.get(key, default))

        # Apply unit conversions
        self.mass_flow = self.mass_flow * 1e-6
        self.flux_in = self.flux_in * 1e-6  # stored as raw, converted here

        # Sample geometry: Size + per-sample locations
        self.Size_x = kwargs.get('Size_x', 0.01)
        self.Size_y = kwargs.get('Size_y', 0.01)
        for i in range(1, NUM_SAMPLES + 1):
            setattr(self, f'Loc_x{i}', kwargs.get(f'Loc_x{i}', INITIAL_RECTS[str(i)]['x']))
            setattr(self, f'Loc_y{i}', kwargs.get(f'Loc_y{i}', INITIAL_RECTS[str(i)]['y']))
        
    def writer_thread(self):
        """Background computation that stores results in the object"""
        
        if not self.use_saved:
            self.status = "running"
            
            try:
                self.stage = 'Loading model'
                if self.run_model in ['plasma','flow']:
                    model = client.load(self.model_file)
 
                self.stage = 'Setting parameters'
                if self.run_model in ['plasma','flow']:
                    if self.run_model == 'plasma':
                        model.parameter('T_chamber', f'{self.temperature}[K]')
                        model.parameter('p_chamber', f'{self.pressure}[torr]')
                        model.parameter('P0', f'{self.power}[W]')
                        model.parameter('f0', f'{self.frequency}[MHz]')
                    elif self.run_model == 'flow':
                        model.parameter('T_chamber', f'{self.temperature}[K]')
                        model.parameter('p_chamber', f'{self.pressure}[torr]')
                        model.parameter('mass_flow', f'{self.mass_flow}[kg/s]')
                        model.parameter('c_Cl_in', f'{self.c_Cl_in}[mol/m^3]')
                        model.parameter('Size_x', f'{self.Size_x}[cm]')
                        model.parameter('Size_y', f'{self.Size_y}[cm]')
                        # Set per-sample locations (normalized to wafer radius)
                        for i in range(1, NUM_SAMPLES + 1):
                            lx = getattr(self, f'Loc_x{i}')
                            ly = getattr(self, f'Loc_y{i}')
                            model.parameter(f'Loc_x{i}', f'{lx / R_WAFER:.3f}')
                            model.parameter(f'Loc_y{i}', f'{ly / R_WAFER:.3f}')
                elif self.run_model in ['etch','performance']:
                    # Compute radial distances for each sample
                    self.radii = [
                        get_radius(getattr(self, f'Loc_x{i}'), getattr(self, f'Loc_y{i}'))
                        for i in range(1, NUM_SAMPLES + 1)
                    ]
    

                self.stage = 'Meshing'
                if self.run_model in ['plasma','flow']:
                    model.mesh()

                self.stage = 'Solving'
                start = time.time()
                if self.run_model in ['plasma','flow']:
                    client.java.showProgress(self.log_file)
                    print(f'Running {self.run_model} model file {self.model_file}')
                    model.solve()
                    self.model = model
                    print(f'{self.model_file} is done')
                elif self.run_model in ['etch','performance']:
                    if self.run_model == 'etch':
                        print('Running etch model script ', self.model_file)
                        result = subprocess.run(['python', '-m', self.model_file, self.log_file, f"{self.etch_time}"] + [f"{r}" for r in self.radii],
                                                check=True, capture_output=True, text=True)
                        print('Loading etch file (etch_output_rate.npy):')
                        self.etch_rate = np.load('outputs/etch_output_rate.npy')
                        self.model = 'Done!'
                    elif self.run_model == 'performance':
                        print(f'Running performance model script {self.model_file}')
                        result = subprocess.run(['python', self.model_file, self.probes, self.log_file],check=True, capture_output=True, text=True)
                        print(f'{self.model_file} is done.')
                        self.conductance = np.load('outputs/conductance.npy')
                        self.conductivity = np.load('outputs/effective_conductivity.npy')
                        self.model = client.load('models/comsol_model.mph')
                end = time.time()
                self.duration = end - start
                self.status = "completed"
            
            except Exception as e:
                print(f'{self.run_model} failed with error',e)
                db.update_param_value(self.row_id, 'status', f'{self.run_model.capitalize()} Failed')
                self.status = f"error: {str(e)}"
                if hasattr(self, 'progress_data'):
                    self.progress_data.append({
                        'timestamp': datetime.now(),
                        'progress': self.current_progress,
                        'stage': f"{self.run_model.capitalize()} Failed",
                        'failed': True
                    })
                self.current_stage = f"{self.run_model.capitalize()} Failed"
        else:
            self.status='load_data'
        
    def reader_thread(self):
        """Background reader thread that monitors for progress statements"""
        processed_lines = 0
        # Regex pattern to match your progress format
        progress_pattern = r"-*\s*Current Progress:\s+(\d*\.?\d+)\s*%\s+-\s+(.+)"
        
        while not self.stop_event.is_set() or self.status == "running":
            try:
                if os.path.exists(self.log_file):
                    with open(self.log_file, 'r') as f:
                        lines = f.readlines()
                                        
                    # Process new lines
                    for line in lines[processed_lines:]:
                        line = line.strip()
                                                
                        # Check if this line matches the progress pattern
                        match = re.match(progress_pattern, line)
                        if match:
                            progress_percent = float(match.group(1))
                            stage_name = match.group(2).strip()
                            
                            # Update current progress
                            self.current_progress = progress_percent
                            self.current_stage = stage_name
                            
                            # Store progress data for plotting
                            self.progress_data.append({
                                'timestamp': datetime.now(),
                                'progress': progress_percent,
                                'stage': stage_name
                            })
                            
                            #print(f"Reader: Progress {progress_percent}% - {stage_name}")
                        
                        processed_lines += 1
                
                time.sleep(0.1)  # Check frequently for updates
                
            except Exception as e:
                print(f"Reader error: {e}")
                time.sleep(0.5)
    
    def start(self):
        """Start the background processing"""
        if os.path.exists(self.log_file):
            os.remove(self.log_file)
        
        self.progress_data = []
        self.current_progress = 0
        self.current_stage = "Starting..."
        self.stop_event.clear()
        
        # Start both threads
        self.writer = threading.Thread(target=self.writer_thread)
        self.reader = threading.Thread(target=self.reader_thread)
        
        self.writer.start()
        self.reader.start()
    
    def stop(self):
        """Stop the background processing"""
        self.stop_event.set()
        self.status = "stopped"

def stop_tasks():
    """Stop and clear all running background tasks."""
    for task in background_tasks.values():
        task.stop()
    background_tasks.clear()


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE & PARAMETER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

# -- load_database_table -------------------------------------------------------
# Reads the parameters table from SQLite, removes duplicate rows, and returns
# a DataFrame ready for display in the AG Grid component.
def load_database_table():
    """
    Reload the parameters table from the database, de-duplicate, and
    return a DataFrame suitable for the AG Grid display.
    """
    database_df = db.load_data('parameters')
    database_df = database_df[DATABASE_DISPLAY_COLUMNS].drop_duplicates(
        subset=DATABASE_DROP_DUPLICATE_COLS, keep='last'
    )
    database_df = db.drop_redundant_nans(database_df)
    
    return database_df

# -- load_params ---------------------------------------------------------------
# Triggered when a user clicks a row in the database table.  Populates all UI
# sliders and the sample-position store with values from the selected row so
# the user can inspect or re-run a previous configuration.
@callback(
    Output('temperature-slider', 'value'),
    Output('pressure-slider', 'value'),
    Output('power-slider', 'value'),
    Output('frequency-slider', 'value'),
    Output('mass-flow-slider', 'value'),
    Output('etch-time-slider', 'value'),
    Output('rect-data','data'),
    Output('selected-probes','data', allow_duplicate=True),
    Input("database-table", "selectedRows"),
    prevent_initial_call=True
)
def load_params(selected_rows):

    if selected_rows is not None and len(selected_rows) > 0:
        selected_data = selected_rows[0]
    else:
        raise PreventUpdate
    # Build rect_data from selected row (loop driven by NUM_SAMPLES)
    rect_data = {}
    for i in range(1, NUM_SAMPLES + 1):
        sid = str(i)
        rect_data[sid] = {
            'x': selected_data[f'Loc_x{i}'],
            'y': selected_data[f'Loc_y{i}'],
            'width': selected_data['Size_x'],
            'height': selected_data['Size_y'],
            'angle': 0,
        }

    if selected_data['probes'] is not None:
        selected_probes = sorted([int(p) - 1 for p in selected_data['probes'].split('-')])
    else:
        selected_probes = list(DEFAULT_SELECTED_PROBES)
    
    return(selected_data['temperature'], selected_data['pressure'], selected_data['power'], selected_data['frequency'], 
           selected_data['mass_flow'],selected_data['etch_time'],rect_data, selected_probes)
    

# -- update_params -------------------------------------------------------------
# The main "Run" callback.  When the user clicks *Update All* (or a derived
# slider changes), this function:
#   1. Assembles the current UI state into a parameter dictionary.
#   2. Walks the model pipeline (plasma → flow → etch → performance) to find
#      the earliest stage whose parameters have changed.
#   3. Checks the database to see if matching results already exist.
#   4. Creates a BackgroundProcessor and starts model execution in a thread.
#   5. Returns updated stores so the polling callbacks can pick up progress.
@callback(
    Output("current-task-store", "data"),
    Output('parameter-values', 'data',allow_duplicate=True),
    Output('running-model', 'data'),
    Output('database-row','data'),
    Output('database-table','rowData'),
    Output('etch-time-data','data'),
    Input('update-all','n_clicks'),
    State('parameter-values','data'), 
    State('temperature-slider', 'value'),
    State('pressure-slider', 'value'),
    State('power-slider', 'value'),
    State('frequency-slider', 'value'),
    State('mass-flow-slider', 'value'),
    State('etch-time-slider', 'value'),
    State('rect-data','data'),
    State('selected-probes','data'),
    Input('cl-inlet-slider','value'),
    Input('flux-in-slider','value'),
    Input('surface-height-slider','value'),
    prevent_initial_call=True
)
def update_params(n_clicks, parameter_values,
                  temperature,pressure,power,frequency,mass_flow, etch_time,
                  sample_params,
                  selected_probes,
                  c_Cl_in, flux_in, surface_height):
        
    if n_clicks == 0:
        raise PreventUpdate
                
    selected_probes = '-'.join([str(p + 1) for p in sorted(selected_probes)])
        
    if selected_probes == '4':
        selected_probes = '1-4'
                
    # Assemble current parameter values from UI state
    new_values = {
        'temperature': temperature,
        'pressure': pressure,
        'power': power,
        'frequency': frequency,
        'mass_flow': mass_flow,
        'c_Cl_in': c_Cl_in,
        'flux_in': flux_in,
        'etch_time': etch_time,
        'surface_height': surface_height,
        'Size_x': sample_params['1']['width'],
        'Size_y': sample_params['1']['height'],
        'probes': selected_probes,
    }
    # Add per-sample location params dynamically
    for i in range(1, NUM_SAMPLES + 1):
        sid = str(i)
        new_values[f'Loc_x{i}'] = sample_params[sid]['x']
        new_values[f'Loc_y{i}'] = sample_params[sid]['y']

    # Determine which model stage needs re-running by walking the pipeline
    # in order and checking for changed parameters.
    run_model = None
    update = False

    found = False
    use_saved = False
    for m in MODEL_ORDER:
        param_set = PARAM_SETS[m]
        for k,v in new_values.items():
            if k not in parameter_values.keys() or v != parameter_values[k]:
                if k in param_set:
                    update = True
                    run_model = m
                    found = True
                    use_saved = False

                    exists, table, row_id = db.check_db(new_values, param_set, m)
                    if exists:
                        use_saved = True
                    break
        if found:
            break
                
    
    
    if run_model is None and not update:
        raise PreventUpdate 
    elif run_model is None:
        return {"task_id": None}, new_values, {}, dash.no_update, dash.no_update, dash.no_update
    
    print(f'Going to run {run_model}')
    
    if row_id is None:
        row_id = db.add_params_to_db(new_values, run_model)
    
    # Stop any existing tasks
    for task in background_tasks.values():
        task.stop()
    background_tasks.clear()
    
    # Create new background task with all current parameter values
    task_id = f"task_{int(time.time())}"
    proc_kwargs = {
        'temperature': temperature,
        'pressure': pressure,
        'power': power,
        'frequency': frequency,
        'mass_flow': mass_flow,
        'c_Cl_in': c_Cl_in,
        'flux_in': flux_in,
        'etch_time': etch_time,
        'surface_height': surface_height,
        'Size_x': (sample_params['1']['width'] or INITIAL_RECTS['1']['width']) * 100.0,
        'Size_y': (sample_params['1']['height'] or INITIAL_RECTS['1']['height']) * 100.0,
        'probes': selected_probes,
        'use_saved': use_saved,
    }
    # Dynamically add per-sample location kwargs
    for i in range(1, NUM_SAMPLES + 1):
        sid = str(i)
        proc_kwargs[f'Loc_x{i}'] = sample_params[sid]['x']
        proc_kwargs[f'Loc_y{i}'] = sample_params[sid]['y']

    processor = BackgroundProcessor(task_id, run_model, row_id, **proc_kwargs)
    background_tasks[task_id] = processor
    processor.start()
    
    print(f'Starting task {task_id}')
    
    database_df = load_database_table()
    
    return {"task_id": task_id}, new_values, {'model':run_model}, {'row_id':row_id}, database_df.to_dict('records'), etch_time
    

# -- refresh_database_table ----------------------------------------------------
# Fires whenever any model result store updates, refreshing the AG Grid so
# newly completed runs appear immediately.
@callback(
    Output('database-table', 'rowData', allow_duplicate=True),
    Input('plasma-data', 'data'),
    Input('flow-data', 'data'),
    Input('etch-data', 'data'),
    Input('performance-data', 'data'),
    prevent_initial_call=True
)
def refresh_database_table(plasma_data, flow_data, etch_data, performance_data):
    return load_database_table().to_dict('records')


# ══════════════════════════════════════════════════════════════════════════════
# MODEL RESULT STORAGE  (pipeline: plasma → flow → etch → performance)
# ══════════════════════════════════════════════════════════════════════════════

# -- store_plasma_results ------------------------------------------------------
# Polled by the interval timer.  When the plasma BackgroundProcessor finishes:
#   • Evaluates species densities and ion velocities from the COMSOL model.
#   • Persists the data to a new plasma_N table in the database.
#   • Exports per-variable text files for downstream models.
#   • Computes the inlet Cl concentration (number density → molar density).
# If the database already contains results for these parameters, loads them
# from the saved table instead.
@callback(
    Output("plasma-data", "data"),
    Output('cl-inlet-slider','value'),
    Output('plasma-variable-select', 'options'),
    Output('plasma-variable-select', 'value'),
    Input("interval-component", "n_intervals"),
    State("current-task-store", "data"),
    State("running-model", "data"),
    State("parameter-values", "data"),
    State("plasma-data", "data"),
    State("database-row","data"),
    prevent_initial_call = True
)
def store_plasma_results(n_intervals, task_data, running_model, parameter_data, plasma_data, row_id):
    
    task_id = task_data.get("task_id") if task_data else None
    running_model = running_model.get('model') if running_model else None    
    
    if not task_id or task_id not in background_tasks or running_model is None or running_model != 'plasma':
        raise PreventUpdate
    
    processor = background_tasks[task_id]
    exists, plasma_table, _ = db.check_db(parameter_data, PARAM_SETS['plasma'], 'plasma')
    
    # Access final results when completed
    if processor.status == "completed" and processor.model:
        z, r, n_wCl, n_wCl_1p , n_wCl2_1p, Viz_wCl_1p, Viz_wCl2_1p = processor.model.evaluate(['z','r','ptp.n_wCl','ptp.n_wCl_1p','ptp.n_wCl2_1p',
                                                                      'ptp.Viz_wCl_1p','ptp.Viz_wCl2_1p'])
        data = pd.DataFrame({'z':z,'r':r,
                             'n_wCl':n_wCl, 'n_wCl_1p': n_wCl_1p, 'n_wCl2_1p': n_wCl2_1p,
                             'Viz_wCl_1p':Viz_wCl_1p, 'Viz_wCl2_1p': Viz_wCl2_1p})
        data = data.sort_values(by=['r','z'],ascending=[True,False])
        
        #add data to database
        new_table = db.create_next_table_and_fill(DB_PATH, 
                                               'plasma_', 
                                               db.plasma_schema, 
                                               data,
                                               row_id['row_id'])
        
        db.update_param_value(row_id['row_id'], 'status', 'Plasma Completed')
        db.update_param_value(row_id['row_id'], 'plasma_duration', processor.duration)
        
        plasma_data['count'] += 1
                
        # Convert from number density to molar density
        c_Cl_in = data[data['z'] == data['z'].max()]['n_wCl'].mean() / AVOGADRO
        
        options = [VAR_TO_NAME[c] for c in data.columns if c not in ['r','z']] 
        
        dfs = []
        for variable in ["n_Cl", "n_Cl+", "n_Cl2+", "Vr_Cl+", "Vr_Cl2+", "Vz_Cl+", "Vz_Cl2+"]:
            filename=f'../outputs/{variable}.txt'
            processor.model.export(variable,filename)
            
            df = pd.read_csv(filename.replace('../',''),skiprows=8,delimiter='\\s+',engine='python',header=None)
            df.columns = ['c1n1x',variable.replace('+','plus')]
            
            dfs.append(df)
        
        df = reduce(lambda x, y: x.merge(y, on='c1n1x'), dfs)                
                        
        new_table = db.create_table_and_fill(DB_PATH, 
                                              new_table.replace('plasma_','plasma_files_'), 
                                               db.plasma_files_schema, 
                                               df)
            
        
    elif plasma_table is not None:        
        data = db.load_data(plasma_table)
        # Convert from number density to molar density
        c_Cl_in = data[data['z'] == data['z'].max()]['n_wCl'].mean() / AVOGADRO
        
        options = [VAR_TO_NAME[c] for c in data.columns if c not in ['r','z']]
        
        plasma_files_table = plasma_table.replace('plasma_','plasma_files_')
        
        data_files = db.load_data(plasma_files_table)
        
        for col in data_files.columns:
            if col != 'c1n1x':
                selection = data_files[['c1n1x',col]]
                selection.columns = ['% c1n1x','Height']
                
                filename=f'outputs/{col.replace("plus","+")}.txt'
                selection.to_csv(filename,sep='\t',index=False)
        
    else:
        raise PreventUpdate
        
    stop_tasks()
    return plasma_data, c_Cl_in, options, options[0]

# -- save_sample_flux_data -----------------------------------------------------
# Determines which grid points in the wafer-surface flux data fall inside each
# sample rectangle (using Shapely polygon containment), labels them with
# sample IDs and geometry, and writes the annotated data to flux_data.csv.
def save_sample_flux_data(flux_data, parameter_data):
    
    flux_data.loc[:,'sample'] = 0
    for col in ['Loc_x','Loc_y','Size_x','Size_y']:
        flux_data.loc[:,col] = np.nan
    
    for sample in range(1, NUM_SAMPLES + 1):
        
        x = parameter_data[f'Loc_x{sample}']
        y = parameter_data[f'Loc_y{sample}']
        size_x = parameter_data['Size_x']
        size_y = parameter_data['Size_y']
        
        p1 = shapely.geometry.Point(x + size_x*np.cos(np.radians(45))/2.0 - size_y*np.sin(np.radians(45))/2.0,
                                    y + size_x*np.sin(np.radians(45))/2.0 + size_y*np.cos(np.radians(45))/2.0)
        p2 = shapely.geometry.Point(x - size_x*np.cos(np.radians(45))/2.0 - size_y*np.sin(np.radians(45))/2.0,
                                    y - size_x*np.sin(np.radians(45))/2.0 + size_y*np.cos(np.radians(45))/2.0)
        p3 = shapely.geometry.Point(x - size_x*np.cos(np.radians(45))/2.0 + size_y*np.sin(np.radians(45))/2.0,
                                    y - size_x*np.sin(np.radians(45))/2.0 - size_y*np.cos(np.radians(45))/2.0)
        p4 = shapely.geometry.Point(x + size_x*np.cos(np.radians(45))/2.0 + size_y*np.sin(np.radians(45))/2.0,
                                    y + size_x*np.sin(np.radians(45))/2.0 - size_y*np.cos(np.radians(45))/2.0)
        
        
        poly = shapely.Polygon([p1,p2,p3,p4,p1])
    
        idx = shapely.contains_xy(poly,flux_data['x'],flux_data['y'])
    
        flux_data.loc[idx,'sample'] = sample
        flux_data.loc[idx,'Loc_x'] = x
        flux_data.loc[idx,'Loc_y'] = y
        flux_data.loc[idx,'Size_x'] = size_x
        flux_data.loc[idx,'Size_y'] = size_y
                
    
    flux_data.to_csv('outputs/flux_data.csv',index=False)

# -- store_flow_results --------------------------------------------------------
# Polled by the interval timer.  When the flow BackgroundProcessor finishes:
#   • Evaluates flux, concentration, and velocity from the COMSOL model.
#   • Exports per-sample flux text files.
#   • Persists data to a new flow_N table and a companion flux_sample table.
#   • Computes the maximum wafer-surface flux for the flux-in slider.
# Falls back to loading from the database if results already exist.
@callback(
    Output("flow-data", "data"),
    Output('flux-in-slider','value'),
    Output('flow-variable-select', 'options'),
    Output('flow-variable-select', 'value'),
    Input("interval-component", "n_intervals"),
    State("current-task-store", "data"),
    State("running-model", "data"),
    State("parameter-values", "data"),
    State("flow-data", "data"),
    State("database-row","data"),
    prevent_initial_call = True
)
def store_flow_results(n_intervals, task_data, running_model, parameter_data, flow_data, row_id):
    
    task_id = task_data.get("task_id") if task_data else None
    running_model = running_model.get('model') if running_model else None    
    
    
    if not task_id or task_id not in background_tasks or running_model is None or running_model != 'flow':
        raise PreventUpdate
    
    processor = background_tasks[task_id]
    exists, flow_table, _ = db.check_db(parameter_data, PARAM_SETS['flow'], 'flow')
    
    # Access final results when completed
    if processor.status == "completed" and processor.model:
        x, y, z, flux, c_Cl, v  = processor.model.evaluate(['x','y','z','tds.tfluxMag_c_Cl','c_Cl','spf.U'])
        data = pd.DataFrame({'x':x,'y':y,'z':z,'flux':flux,'c_Cl':c_Cl, 'v': v})

        flux_sample_dfs = []
        for sample in range(1, NUM_SAMPLES + 1):          
            processor.model.export(f'Flux of sample {sample}', f'../outputs/Flux_sample{sample}_wor.txt')
            flux_sample_df = pd.read_csv(f'outputs/Flux_sample{sample}_wor.txt',skiprows=8,
                                         delimiter='\\s+',engine='python',header=None)
            flux_sample_df.columns = [f'surf{sample+2}x',f'surf{sample+2}y','Color']
            flux_sample_dfs.append(flux_sample_df)
        flux_sample_df = pd.concat(flux_sample_dfs,axis=0).reset_index(drop=True)
        
        #add data to database
        new_table = db.create_next_table_and_fill(DB_PATH, 
                                               'flow_', 
                                               db.flow_schema, 
                                               data,
                                               row_id['row_id'])
        
        new_flux_table = db.create_table_and_fill(DB_PATH, 
                                                new_table.replace('flow_',f'flow_flux_sample_'), 
                                                db.flux_sample_schema, 
                                                flux_sample_df)
        
        print('Flow model completed, data stored in database.')
        db.update_param_value(row_id['row_id'], 'status', 'Flow Completed')
        db.update_param_value(row_id['row_id'], 'flow_duration', processor.duration)
        
        flux_in = data[data['z'] == SURFACE_Z]['flux'].max()/1e-6
                
        flux_data = data[data['z'] == SURFACE_Z].copy()
            
        save_sample_flux_data(flux_data, parameter_data)
        
        flow_data['count'] += 1
    elif flow_table is not None:
        data = db.load_data(flow_table)
        flux_in = data[data['z'] == SURFACE_Z]['flux'].max()/1e-6
        flux_data = data[data['z'] == SURFACE_Z].copy()
        
        flux_sample_data = db.load_data(flow_table.replace('flow_','flow_flux_sample_'))
        for sample in range(1, NUM_SAMPLES + 1):
            flux_sample_df = flux_sample_data[~flux_sample_data[f'surf{sample+2}x'].isnull()]
            flux_sample_df = flux_sample_df[[f'surf{sample+2}x',f'surf{sample+2}y','Color']]
            flux_sample_df.columns = [f'% surf{sample+2}x',f'surf{sample+2}y','Color']
            filename = f'outputs/Flux_sample{sample}_wor.txt'
            flux_sample_df.to_csv(filename,sep='\t',index=False)
        
        save_sample_flux_data(flux_data, parameter_data)
    else:
        raise PreventUpdate
        
    options = [VAR_TO_NAME[c] for c in data.columns if c not in ['x','y','z']] 
        
    stop_tasks()
    return flow_data, flux_in, options, options[0]

# -- _save_surface_file --------------------------------------------------------
# Writes the etched surface profile (electrode, x, y, surface height) to the
# standard output file used by downstream visualisation and the performance model.
def _save_surface_file(surface_df):
    """Write etch surface data to the standard output file."""
    data_save = np.column_stack([
        surface_df['electrode'].values,
        surface_df['x'].values,
        surface_df['y'].values,
        surface_df['surface'].values,
    ])
    np.savetxt(
        'outputs/etching_final_surface.txt', data_save,
        fmt=['%d', '%.8e', '%.8e', '%.8e'], delimiter='\t',
        header='Electrode #\tX\tY\tFlux', comments='',
    )

# -- store_etch_results --------------------------------------------------------
# Polled by the interval timer.  When the etch BackgroundProcessor finishes:
#   • Reshapes the 3-D etch-rate array (samples × x × y) into a flat DataFrame.
#   • Reads per-sample final-surface CSVs and persists them to the database.
#   • Writes the selected sample's surface file for the performance model.
# Falls back to loading from the database if results already exist.
@callback(
    Output("etch-data", "data"),
    Output('surface-height-slider','value',allow_duplicate=True),
    Output('roughness-display','value'),
    Output('nonuniformity-display','value'),
    Input("interval-component", "n_intervals"),
    State('etch-sample-selector','value'),
    State("current-task-store", "data"),
    State("running-model", "data"),
    State("parameter-values", "data"),
    State("etch-data", "data"),
    State("database-row","data"),
    prevent_initial_call = True
)
def store_etch_results(n_intervals, etch_sample, task_data, running_model, parameter_data, etch_data, row_id):
    
        
    task_id = task_data.get("task_id") if task_data else None
    running_model = running_model.get('model') if running_model else None    

    
    if (not task_id or task_id not in background_tasks or running_model is None or running_model != 'etch'):
        raise PreventUpdate
    
    processor = background_tasks[task_id]
    exists, etch_table, _ = db.check_db(parameter_data, PARAM_SETS['etch'], 'etch')

    
    # Access final results when completed
    if processor.status == "completed" and processor.model:
        
        etch_rates = processor.etch_rate
        
        N, X, Y = etch_rates.shape
        
        n_idx, x_idx, y_idx = np.meshgrid(
            np.arange(N),
            np.arange(X),
            np.arange(Y),
            indexing='ij'
        )

        data = pd.DataFrame({
            'sample': n_idx.ravel(),
            'x': x_idx.ravel(),
            'y': y_idx.ravel(),
            'etch_rate': etch_rates.ravel()
        })
        
        etch_data['count'] += 1
                
        new_table = db.create_next_table_and_fill(DB_PATH, 
                                               'etch_', 
                                               db.etch_schema, 
                                               data,
                                               row_id['row_id'])
        
        db.update_param_value(row_id['row_id'], 'status', 'Etch Completed')
        db.update_param_value(row_id['row_id'], 'etch_duration', processor.duration)
        
        for sample in range(1, NUM_SAMPLES + 1):
            etch_surface = pd.read_csv(f'outputs/etching_final_surface_{sample}.txt',delimiter='\t')
            etch_surface.columns = ['electrode','x','y','surface']
            
            new_surface_table = db.create_table_and_fill(DB_PATH, 
                                                new_table.replace('etch_',f'etch_surface_sample{sample}_'), 
                                                db.surface_schema, 
                                                etch_surface)
            
            if int(sample) == int(etch_sample):
                _save_surface_file(etch_surface)
                surface_height = np.nanmean(etch_surface['surface'].values)
                result = sr.process_etching_surface('outputs/etching_final_surface.txt')
                roughness = result['roughness']
                nonuniformity = result['nonuniformity']
                print('Updating roughness and nonuniformity in database:', roughness, nonuniformity)
                db.update_param_value(row_id['row_id'], 'roughness', roughness)
                db.update_param_value(row_id['row_id'], 'nonuniformity', nonuniformity)
                
    elif etch_table is not None:
        data = db.load_data(etch_table)
        
        surface_data = db.load_data(etch_table.replace('etch_', f'etch_surface_sample{etch_sample}_'))
        _save_surface_file(surface_data)
        surface_height = np.nanmean(surface_data['surface'].values)
        result = sr.process_etching_surface('outputs/etching_final_surface.txt')
        roughness = result['roughness']
        nonuniformity = result['nonuniformity']
    else:
        raise PreventUpdate
        
        
    stop_tasks()
    return etch_data, surface_height, f"{roughness:.2f}", f"{nonuniformity:.2f}"

# -- switch_sample -------------------------------------------------------------
# Triggered when the user picks a different sample in the etch-sample dropdown.
# Loads that sample's surface profile from the database and writes it to disk
# so the performance model and 3-D animation use the correct surface.
@callback(
    Output('surface-height-slider','value',allow_duplicate=True),
    Output('roughness-display','value', allow_duplicate=True),
    Output('nonuniformity-display','value', allow_duplicate=True),
    Input('etch-sample-selector','value'),
    State("parameter-values", "data"),
    prevent_initial_call = True
)
def switch_sample(etch_sample,parameter_data):
        
    exists, etch_table, _ = db.check_db(parameter_data, PARAM_SETS['etch'], 'etch')
    
    surface_data = db.load_data(etch_table.replace('etch_', f'etch_surface_sample{etch_sample}_'))
    _save_surface_file(surface_data)
     
    surface_height = np.nanmean(surface_data['surface'].values)   
        
    result = sr.process_etching_surface('outputs/etching_final_surface.txt')
    roughness = result['roughness']
    nonuniformity = result['nonuniformity'] 
        
    return surface_height, f"{roughness:.2f}", f"{nonuniformity:.2f}"

# -- store_performance_results -------------------------------------------------
# Polled by the interval timer.  When the performance BackgroundProcessor
# finishes:
#   • Reads conductance and effective conductivity from saved .npy files.
#   • Evaluates voltage and conductivity fields from the COMSOL model.
#   • Persists data and updates the database status to "Successfully Completed".
# Falls back to loading from the database if results already exist.
@callback(
    Output("performance-data", "data"),
    Output('conductance-display','value'),
    Output('effective-conductivity-display','value'),
    Input("interval-component", "n_intervals"),
    State("current-task-store", "data"),
    State("running-model", "data"),
    State("parameter-values", "data"),
    State("performance-data", "data"),
    State("database-row","data"),
    State('roughness-display','value'),
    State('nonuniformity-display','value'),
    prevent_initial_call = True
)
def store_performance_results(n_intervals, task_data, running_model, parameter_data, performance_data, row_id, roughness, nonuniformity):
    
    task_id = task_data.get("task_id") if task_data else None
    running_model = running_model.get('model') if running_model else None    
    
    
    if not task_id or task_id not in background_tasks or running_model is None or running_model != 'performance':
        raise PreventUpdate
    
    processor = background_tasks[task_id]
    exists, performance_table, _ = db.check_db(parameter_data, PARAM_SETS['performance'], 'performance')
    
    # Access final results when completed
    if processor.status == "completed" and processor.model:
        
        conductance = processor.conductance
        conductivity = processor.conductivity
        
        x,y,R,I,V0,V, sigma = processor.model.evaluate(['x','y','ec.R11', 'ec.I0_1','ec.V0_1','V', 'ec.sigmaxx'])
                
        #data = pd.DataFrame({'x':x,'y':y,'R':R,'I':I,'V0':V0,'V':V})
        data = pd.DataFrame({'x':x,'y':y,'V':V,'conductivity':sigma})
        
        
        #add data to database
        new_table = db.create_next_table_and_fill(DB_PATH, 
                                               'performance_', 
                                               db.performance_schema, 
                                               data,
                                               row_id['row_id'])
        
        db.update_param_value(row_id['row_id'], 'status', 'Succesfully Completed')
        db.update_param_value(row_id['row_id'], 'performance_duration', processor.duration)
        print('Updating conductance and effective conductivity in database:', conductance, conductivity)
        db.update_param_value(row_id['row_id'], 'conductance', conductance)
        db.update_param_value(row_id['row_id'], 'effective_conductivity', conductivity)
        db.update_param_value(row_id['row_id'], 'roughness', roughness)
        db.update_param_value(row_id['row_id'], 'nonuniformity', nonuniformity)
        
        performance_data['count'] += 1
    elif performance_table is not None:
        data = db.load_data(performance_table)
        
        conductance = db.get_param_value(row_id['row_id'], 'conductance')
        conductivity = db.get_param_value(row_id['row_id'], 'effective_conductivity')
        print('Loaded conductance and effective conductivity from database:', conductance, conductivity)
    else:
        raise PreventUpdate
        
    options = [VAR_TO_NAME[c] for c in data.columns if c not in ['x','y']]  
        
    stop_tasks()
    return performance_data, f"{conductance:.0f}", f"{conductivity:.0f}"


# ══════════════════════════════════════════════════════════════════════════════
# PROGRESS MONITORING
# ══════════════════════════════════════════════════════════════════════════════

# -- update_progress_display ---------------------------------------------------
# Polled by the interval timer.  Reads the active BackgroundProcessor's
# progress percentage and stage name, then routes them to the correct model's
# progress bar.  All other models' progress bars show "No active task".
# On failure, refreshes the database table so the user sees the error status.
@callback(
    [Output("plasma-progress-bar", "value"),
     Output("plasma-progress-bar", "color"),
     Output("plasma-progress-text", "children"),
     Output("plasma-current-stage", "children"),
     Output("flow-progress-bar", "value"),
     Output("flow-progress-bar", "color"),
     Output("flow-progress-text", "children"),
     Output("flow-current-stage", "children"),
     Output("etch-progress-bar", "value"),
     Output("etch-progress-bar", "color"),
     Output("etch-progress-text", "children"),
     Output("etch-current-stage", "children"),
     Output("performance-progress-bar", "value"),
     Output("performance-progress-bar", "color"),
     Output("performance-progress-text", "children"),
     Output("performance-current-stage", "children"),
     Output("database-table", "rowData", allow_duplicate=True)],
    [Input("interval-component", "n_intervals"),
     Input("current-task-store", "data"),
     Input("running-model", "data")],
    prevent_initial_call=True
)
def update_progress_display(n_intervals, task_data, running_model):
    
    task_id = task_data.get("task_id") if task_data else None
    
        
    if not task_id or task_id not in background_tasks:
        idle = (0, "primary", "No active task", "")
        return *idle, *idle, *idle, *idle, dash.no_update
    
    processor = background_tasks[task_id]
    
    # Build progress values for the active model
    progress_value = processor.current_progress
    progress_text = f"{progress_value}%"
    current_stage = f"Stage: {processor.current_stage}"
    failed = "Failed" in processor.current_stage
    bar_color = "danger" if failed else "primary"
    table_update = load_database_table().to_dict('records') if failed else dash.no_update

    active = (progress_value, bar_color, progress_text, current_stage)
    idle = (0, "primary", "No active task", "")

    # Place active progress in the slot matching the running model
    active_model = running_model.get("model")
    result = tuple(
        val
        for m in MODEL_ORDER
        for val in (active if m == active_model else idle)
    )
    return *result, table_update
    


# ══════════════════════════════════════════════════════════════════════════════
# RESULT VISUALIZATION
# ══════════════════════════════════════════════════════════════════════════════

# -- display_results -----------------------------------------------------------
# Rebuilds all five result plots whenever parameter values change or a model
# run completes.  Each model's plot is only rendered when its data exists in
# the database:
#   • Plasma  → vertical cross-section heatmap (r × z).
#   • Flow    → wafer-surface heatmap (x × y at z = H_WAFER).
#   • Etch    → 2×2 subplot grid of per-sample etch-rate maps.
#   • Performance → conductivity heatmap + voltage heatmap.
# The selected etch-rate map is also forwarded to the 3-D animation store.
@callback(
    Output("plasma-output-graph", "figure"),
    Output("flow-output-graph", "figure"),
    Output("etch-output-graph-1", "figure"),
    Output("performance-output-graph-1", "figure"),
    Output("performance-output-graph-2", "figure"),
    Output('rate-store', 'data'),
    Input('parameter-values','data'),
    Input('plasma-variable-select','value'),
    Input('flow-variable-select','value'),
    #Input('performance-variable-select','value'),
    Input("etch-data", "data"),
    Input('etch-sample-selector','value'),
    Input('performance-data','data'),
    prevent_initial_call = True
)
def display_results(parameter_data, plasma_variable, flow_variable, etch_data, etch_sample,performance_data):
    
    exists, plasma_table, _ = db.check_db(parameter_data, PARAM_SETS['plasma'], 'plasma')
    exists, flow_table, _ = db.check_db(parameter_data,PARAM_SETS['flow'], 'flow')
    exists, etch_table, _ = db.check_db(parameter_data,PARAM_SETS['etch'], 'etch')
    exists, performance_table, _ = db.check_db(parameter_data,PARAM_SETS['performance'], 'performance')
    
    
    if plasma_table is None and flow_table is None and etch_table is None and performance_table is None:
        raise PreventUpdate
    
    plasma_fig = {}
    flow_fig = {}
    etch_fig = {}
    performance_fig1 = {}
    performance_fig2 = {}
    rate = 0
    selected_rate = 0
    
    if plasma_table is not None and plasma_variable is not None:    
        
        data = db.load_data(plasma_table)
        
        x_scatter = data['r']
        y_scatter = data['z']
        z_scatter = data[NAME_TO_VAR[plasma_variable]]
        
        grid_x, grid_y = np.mgrid[0:0.2:250j, 0:0.2:250j]
        
        grid_z = griddata((x_scatter, y_scatter), z_scatter, (grid_x, grid_y), method='linear')
        
        idx = ~shapely.contains_xy(poly,grid_x,grid_y)
        
        grid_z_inside = grid_z.copy()
        grid_z_inside[idx] = np.nan
        
        plasma_fig = px.imshow(grid_z_inside.T, 
                                origin='lower', 
                                color_continuous_scale=px.colors.sequential.Viridis,
                                aspect='auto')

        plasma_fig.update_layout(
                title="Vertical Cross-Section",
                xaxis_title="r",
                yaxis_title="z",
                coloraxis_colorbar={"title": plasma_variable.split('(')[-1].strip(')')}
            )

        plasma_fig.update_xaxes(
            showticklabels=False
        )
        
        plasma_fig.update_yaxes(
            showticklabels=False
        )
        
    if flow_table is not None and flow_variable is not None:    
        
        data = db.load_data(flow_table)
        
        data = data[ (data['z'] == H_WAFER) ]

        x_scatter = data['x']
        y_scatter = data['y']
        z_scatter = data[NAME_TO_VAR[flow_variable]]
        grid_x, grid_y = np.mgrid[-0.1:0.1:250j, -0.1:0.1:250j]
        grid_z = griddata((x_scatter, y_scatter), z_scatter, (grid_x, grid_y), method='linear')
        
        flow_fig = px.imshow(grid_z.T, 
                             origin='lower', 
                             color_continuous_scale=px.colors.sequential.Viridis,
                             aspect='auto')
        
        flow_fig.update_layout(
                title="Wafer Surface",
                xaxis_title="x",
                yaxis_title="y",
                coloraxis_colorbar={"title": flow_variable.split('(')[-1].strip(')')}
            )

        flow_fig.update_xaxes(
            showticklabels=False
        )
        
        flow_fig.update_yaxes(
            showticklabels=False
        )
        
        
    if etch_table is not None:
        data = db.load_data(etch_table)
        
        etch_fig = make_subplots(rows=2, cols=2,
                                 subplot_titles=("Sample 1", "Sample 2", 
                                                 "Sample 3", "Sample 4"))
        
        for sample in range(NUM_SAMPLES):
            subset = data[ data['sample'] == sample ].sort_values('y',ascending=False).sort_values('x',ascending=True)
                 
            subset = pd.pivot_table(subset, index='y',columns='x',values='etch_rate').values
            
            rate = subset.tolist()
            if sample == int(etch_sample) - 1:
                selected_rate = rate
            
            etch_fig.add_trace(px.imshow(subset, 
                                origin='lower', 
                                color_continuous_scale=px.colors.sequential.Viridis,
                                labels={'color': 'Etch Rate (nm/s)', 'x': 'x (mm)', 'y': 'y (mm)'},
                                aspect='equal').data[0],
                               row = sample // 2 + 1,
                               col = sample % 2 + 1)
            
        etch_fig.update_layout(title_text="Etch Rates")
        
        etch_fig.update_layout(
            yaxis_scaleanchor="x",
            yaxis2_scaleanchor="x2",
            yaxis3_scaleanchor='x3',
            yaxis4_scaleanchor='x4',
            yaxis_constrain="domain",
            yaxis2_constrain="domain",
            yaxis3_constrain='domain',
            yaxis4_constrain='domain',
            coloraxis_colorbar={"title": 'Etch Rate (nm/s)'}
        )
        
    if performance_table is not None:
        data = db.load_data(performance_table)
             

        x_scatter = data['y']
        y_scatter = data['x']
        V_scatter = data['V']
        conductivity_scatter = data['conductivity']
        
        
        #grid_x, grid_y = np.mgrid[0:np.max(x_scatter):250j, 0:np.max(y_scatter):250j] 
        grid_x, grid_y = np.mgrid[0:np.max(x_scatter):500j, 0:np.max(y_scatter):500j] 
        
        grid_x_int, grid_y_int = np.mgrid[0:500:1, 0:500:1] 
                
        grid_V = griddata((x_scatter, y_scatter), V_scatter, (grid_x, grid_y), method='linear')
        grid_conductivity = griddata((x_scatter, y_scatter), conductivity_scatter, (grid_x, grid_y), method='linear')
                        
                
        
        probe_x = np.concatenate([np.arange(115,141),np.arange(240,266), np.arange(365,391)])

        probe_y = np.concatenate([np.arange(0,106),np.arange(395,500)])
        
        idx = np.isin(grid_x_int,probe_x) & np.isin(grid_y_int,probe_y)
        

        try:
            cmax = 1.1*grid_conductivity[~idx].max()
        except ValueError:
            cmax = grid_conductivity.max()
        
        grid_conductivity[idx] = cmax
                        
        performance_fig1 = px.imshow(grid_conductivity, 
                                origin='lower', 
                                color_continuous_scale=px.colors.sequential.Viridis,
                                zmax=cmax,
                                aspect='equal')

        performance_fig1.update_layout(
                title='Conductivity',
                xaxis_title="x",
                yaxis_title="y",
                coloraxis_colorbar={"title": 'Conductivity (S/m)'}
            )
        
        performance_fig2 = px.imshow(grid_V, 
                                origin='lower', 
                                color_continuous_scale=px.colors.sequential.Viridis,
                                aspect='equal')

        performance_fig2.update_layout(
                title='Voltage',
                xaxis_title="x",
                yaxis_title="y",
                coloraxis_colorbar={"title": 'Voltage (V)'}
            )
        

    return plasma_fig, flow_fig, etch_fig, performance_fig1, performance_fig2, selected_rate
        

# -- make_split_colorscale (helper) --------------------------------------------
# Builds a Plotly-compatible colorscale that splits at a threshold value,
# transitioning from an "orange" palette below (TI layer) through white at
# the interface to a "blue" palette above (Al layer).  Used by the etch
# 3-D surface animation.
# --- Split colorscale function ---
def make_split_colorscale(threshold, z_min, z_max,
                           below_color_start='rgb(210,100,0)',    # Dark orange (low values)
                           mid_color='rgb(255,255,255)',           # White at threshold
                           above_color_end='rgb(0,50,180)'):       # Dark blue (high values)
    """
    Creates a continuous-looking split colorscale that transitions through
    a midpoint color at the threshold.

    Parameters
    ----------
    threshold : float
        The value at which the colorscale splits
    z_min : float
        Minimum value of the data
    z_max : float
        Maximum value of the data
    below_color_start : str
        Color at the minimum value (bottom of orange scale)
    mid_color : str
        Color at the threshold - shared by both scales for continuity
    above_color_end : str
        Color at the maximum value (top of blue scale)
    """
    # Normalize threshold to [0, 1]
    t = (threshold - z_min) / (z_max - z_min)
    t = np.clip(t, 0.001, 0.999)

    colorscale = [
        [0.0, below_color_start],  # Dark orange at bottom
        [t,   mid_color],          # White at threshold (orange side)
        [t,   mid_color],          # White at threshold (blue side) - same color = continuous
        [1.0, above_color_end],    # Dark blue at top
    ]

    return colorscale

# -- update_plot (etch 3-D animation) ------------------------------------------
# Renders one frame of the etch animation.  Given the current frame index and
# etch-rate grid, it:
#   1. Computes the surface height Z_top by integrating the etch rate forward.
#   2. Applies a two-layer rate (with rate_ratio_lower for the TI layer).
#   3. Adds probe-cover regions that stay at full thickness.
#   4. Plots the 3-D surface with a split orange/blue colorscale, side walls,
#      TI interface plane, and boundary outline.
#   5. Preserves the user's camera orientation between frames.
@callback(
    [Output('etch-output-graph-2', 'figure'),
     Output('frame-display', 'children'),
     Output('z-top-store', 'data'),
     Output('t-store','data')], 
    [Input('current-frame', 'data'),
     Input('is-playing', 'data')],
    [State('z-top-store', 'data'),
     State('t-store','data'),
     State('rate-store','data'),
     State('etch-output-graph-2', 'relayoutData'),
     State('etch-time-data', 'data')]  
)
def update_plot(current_frame, is_playing, previous_z_top, previous_time, rate, relayout_data, etch_time):
    
    if not is_playing:
        raise PreventUpdate
    
    save_interval = etch_time / 50.0
    times = np.arange(0, etch_time+save_interval, save_interval)
    
    t = times[current_frame]
    rate = np.array(rate)
        
    # Grid definition
    nx = int(length_x / pixel_xy) +1
    ny = int(length_y / pixel_xy) +1
    xi = np.linspace(-length_x/2, length_x/2, nx)
    yi = np.linspace(-length_y/2, length_y/2, ny)
    XI, YI = np.meshgrid(xi, yi)
        
    # Derived
 #   height_mean = thick_sample
        
    # Compute new top
    # Time increment for this frame
    t_prev = times[current_frame - 1] if current_frame > 0 else 0
    t_curr = times[current_frame]
    nsteps = int((t_curr - t_prev) / dt)
    sigma0 = 12.7
    
    if previous_z_top is None or t == 0:
        rng = np.random.default_rng(seed=42)
        previous_z_top = height_mean + (rng.random(size=(ny, nx, ))*2 - 1) * noise_amp
    
    # Bottom surface (flat)
    Z_bot = np.zeros_like(XI)
    
    Z_top = np.array(previous_z_top)
    
    
    # Compute initial boundary coords (row-major perimeter)
    boundary = []
    boundary += [0*nx + i for i in range(nx)]
    boundary += [j*nx + (nx-1) for j in range(1, ny)]
    boundary += [(ny-1)*nx + i for i in range(nx-2, -1, -1)]
    boundary += [j*nx + 0 for j in range(ny-2, 0, -1)]
    pts_top0 = np.c_[XI.ravel(), YI.ravel(), Z_top.ravel()]
    boundary_coords = pts_top0[boundary]
    
    
    if rate.shape != Z_top.shape:
        rate = np.zeros(Z_top.shape)
    
    rate = np.nan_to_num(rate.astype(float), nan=0.0)
    
    
    # Integrate with dt so the rate switches exactly when crossing the interface
    for _ in range(nsteps):
        if np.sum(rate) == 0:
            eff = rate
        else:
            eff = rate +   (np.random.rand(ny, nx)*2 - 1)*sigma0/np.sqrt((times[-1]-times[0])/dt)           # base (upper-layer) rate
        eff[Z_top <= thick_sample-thick_metal] *= rate_ratio_lower  # slow in lower layer
        Z_top = np.maximum(Z_top - eff * dt, Z_bot)    # do not go below bottom

    
    flag_cover = rate*0.0
    flag_cover[75:80, :20] = 1.0
    flag_cover[75:80, -20:] = 1.0
    flag_cover[50:55, :20] = 1.0
    flag_cover[50:55, -20:] = 1.0
    flag_cover[25:31, :20] = 1.0
    flag_cover[25:31, -20:] = 1.0
    Z_top_cover = Z_top*(1.0-flag_cover) +flag_cover*thick_sample
    
    # Create figure 
    fig = go.Figure()
    
    # Plot filled side walls
    for Xw, Yw, Zw in side_wall_coords(Z_top, xi, yi, Z_bot, ny):

        grey_colors = np.full(Zw.shape, 0.5)

        fig.add_trace(go.Surface(
            x=Xw,
            y=Yw, 
            z=Zw,
            surfacecolor=grey_colors,
            colorscale=[[0, 'grey'], [1, 'grey']],
            opacity=0.6,
            showscale=False,
            visible=True
        ))
    
    red_colorscale = [[0, 'red'], [1, 'red']]
    uniform_surfacecolor = np.zeros(shape=Z_bot.shape)
    
    if np.min(Z_top_cover) >= np.min(Z_bot+thick_sample-thick_metal):
        #Add TI surface
        fig.add_trace(go.Surface(
            x = XI,
            y = YI,
            z = Z_bot+thick_sample-thick_metal,
            colorscale=red_colorscale,
            surfacecolor=uniform_surfacecolor,
            opacity=0.5,
            showscale=False
        ))
    
    try:
        cmax = Z_top_cover[Z_top_cover < 0.99*thick_sample].max()
    except ValueError:
        cmax = Z_top_cover.max()
    
    threshold = (Z_bot+thick_sample-thick_metal)[0][0]
    z_min = 0.999 * Z_top_cover.min()
    z_max = 1.001 * cmax
    
    split_colorscale = make_split_colorscale(
        threshold=threshold,
        z_min=z_min,
        z_max=z_max,
        below_color_start='rgb(180,80,0)',    # Dark orange
        mid_color='rgb(255,255,255)',          # White at threshold
        above_color_end='rgb(0,50,180)',       # Dark blue
    )
    
    # Add main surface
    fig.add_trace(go.Surface(
        x=XI,
        y=YI,
        z=Z_top_cover,
        colorscale=split_colorscale,
        opacity=0.75,
        cmin=0.999*Z_top_cover.min(),
        cmax=1.001*cmax,
        #surfacecolor=facecolors,
        showscale=True,
        name='Top Surface',
        lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0, roughness=1.0),
        colorbar=dict(
            title="Height (nm)",
            x=1.02
        )
    ))
    
 
    # Add boundary outline
    fig.add_trace(go.Scatter3d(
        x=boundary_coords[:,0],
        y=boundary_coords[:,1], 
        z=boundary_coords[:,2],
        mode='lines',
        line=dict(color='black', width=4),
        name='Boundary',
        showlegend=False
    ))
    
    camera_settings = dict(eye=dict(x=1.0, y=1.0, z=1.0))  # default
    if relayout_data and 'scene.camera' in relayout_data:
        camera_settings = relayout_data['scene.camera']
    
    # Update layout
    fig.update_layout(
        title=f"t = {t:.2f} s (Pause to enable zoom and rotate). Blue = Al. Orange = TI.",
        scene=dict(
            xaxis_title="X (mm)",
            yaxis_title="Y (mm)", 
            zaxis_title="Z (nm)",
            xaxis=dict(range=None, autorange=True),
            yaxis=dict(range=None, autorange=True),
            zaxis=dict(range=None, autorange=True),
            camera=camera_settings,
            #aspectmode='cube'
            dragmode='turntable',
            aspectratio=dict(x=1, y=1, z=0.3)
        ),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    
    frame_text = f"Frame: {current_frame + 1}/{len(times)} (t = {t:.2f}s)"
    
    return fig, frame_text, Z_top.tolist(), t

# -- control_playback ----------------------------------------------------------
# Toggles the is-playing flag when the user clicks Play or Pause.
# Control playing state
@callback(
    Output('is-playing', 'data'),
    [Input('play-button', 'n_clicks'),
     Input('pause-button', 'n_clicks')],
    prevent_initial_call=True
)
def control_playback(play_clicks, pause_clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return False
    
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == 'play-button':
        return True
    elif button_id == 'pause-button':
        return False
    
    return False

# -- update_frame --------------------------------------------------------------
# Advances the current frame counter on each interval tick (when playing),
# or resets it to 0 when the Reset button is clicked.
# Reset frame
@callback(
    Output('current-frame', 'data'),
    [Input('reset-button', 'n_clicks'),
     Input('frame-interval-component', 'n_intervals')],
    [State('is-playing', 'data'),
     State('current-frame', 'data'),
     State('etch-time-data', 'data')],
    prevent_initial_call=True
)
def update_frame(reset_clicks, n_intervals, is_playing, current_frame, etch_time):
    ctx = dash.callback_context
    if not ctx.triggered:
        return 0
        
    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    
    if button_id == 'reset-button':
        return 0
    elif button_id == 'frame-interval-component' and is_playing:
        
        save_interval = etch_time / 50.0
        times = np.arange(0, etch_time+save_interval, save_interval)
        
        return (current_frame + 1) % len(times)
    
    return current_frame


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE PLACEMENT UI  (wafer platform)
# ══════════════════════════════════════════════════════════════════════════════

# -- create_plot (helper) ------------------------------------------------------
# Builds the interactive wafer-platform figure showing the circular platform,
# the outlet, and one draggable rectangle per sample.  Users can drag sample
# rectangles to set their positions directly on the plot.
def create_plot(xes, yes, widths, heights, angles):
    """Create the plot with the rectangle"""
    fig = go.Figure()
    
    # Add a circle shape
    fig.add_shape(
        type='circle',
        editable=False,
        xref='x', yref='y',
        x0=-0.1, y0=-0.1, x1=0.1, y1=0.1,  # Coordinates of the bounding box
        line=dict(color='black', width=2),
    )
    
    fig.add_annotation(x=-0.1/np.sqrt(2), y=0.1/np.sqrt(2),
            text="Platform",
            showarrow=True,
            arrowhead=1)


    for i in range(len(xes)):
        x0 = xes[i] - widths[i]/2.0
        y0 = yes[i] - heights[i]/2.0
        x1 = xes[i] + widths[i]/2.0
        y1 = yes[i] + heights[i]/2.0
        
        if angles[i] == 0:
            # Use regular rectangle shape for no rotation (allows dragging)
            fig.add_shape(
                    type="rect",
                    x0=x0, y0=y0, x1=x1, y1=y1,
                    line=dict(color=SAMPLE_COLORS[str(i+1)], width=2),
                    fillcolor=SAMPLE_COLORS[str(i+1)],
                    editable=True,
                    name=f"Sample {i+1}"
                )

    
    fig.add_shape(
        type='circle',
        xref='x', yref='y',
        editable=False,
        x0=0.12, y0=-0.02, x1=0.16, y1=0.02,  # Coordinates of the bounding box
        line=dict(color='black', width=2),
    )
    
    fig.add_annotation(x=0.14, y=0.02,
            text="Outlet",
            showarrow=True,
            arrowhead=1)
    
    fig.update_layout(
        title="Drag the samples or edit the values in the input boxes",
        title_subtitle_text="Set Angle to 0 to enable dragging.",
        xaxis_title="X",
        yaxis_title="Y",
        plot_bgcolor='white',
        yaxis=dict(
            scaleanchor="x",  # Anchor y-axis to x-axis
            scaleratio=1      # Set 1 unit on y-axis to equal 1 unit on x-axis
        )
    )
    fig.update_xaxes(
        zerolinecolor='lightgrey',
        mirror=True,
        ticks='outside',
        showline=True,
        linecolor='black',
        gridcolor='lightgrey'
    )
    fig.update_yaxes(
        zerolinecolor='lightgrey',
        mirror=True,
        ticks='outside',
        showline=True,
        linecolor='black',
        gridcolor='lightgrey'
    )     
    
    fig.update_xaxes(range=[-0.17, 0.17])
    fig.update_yaxes(range=[-0.12, 0.12])
    
    
    return fig

# -- update_inputs -------------------------------------------------------------
# Synchronises the sample-position input boxes, the interactive plot, and the
# rect-data store.  Three trigger paths:
#   • interactive-plot: a rectangle was dragged → update input boxes + store.
#   • rect-id: the user selected a different sample → load its values.
#   • x/y/width/height inputs: manual entry → update store.
# Also propagates a uniform width/height to all samples.
@callback(
    [Output('x-center', 'value'),
     Output('y-center', 'value'),
     Output('width-input', 'value'),
     Output('height-input', 'value'),
     Output('rect-id','value'),
     Output('rect-data','data',allow_duplicate=True),
     Output('output-color-box', 'style')],
    [Input('rect-id','value'),
     State('rect-data','data'),
     Input('x-center', 'value'),
     Input('y-center', 'value'),
     Input('width-input', 'value'),
     Input('height-input', 'value'),
     Input('interactive-plot', 'relayoutData')],
    prevent_initial_call=True
)
def update_inputs(rect_id, rect_data,
                           x_input, y_input, width_input, height_input,
                           relayout_data):
    ctx = callback_context
    
    rect_id = rect_id if rect_id is not None else '1'
    
    x0_input = x_input - width_input/2.0
    y0_input = y_input - height_input/2.0
    x1_input = x_input + width_input/2.0
    y1_input = y_input + height_input/2.0
    
    # Determine what triggered the callback
    if not ctx.triggered:
        # Initial load
        x, y, width, height = INITIAL_RECTS[rect_id]['x'], INITIAL_RECTS[rect_id]['y'], INITIAL_RECTS[rect_id]['width'], INITIAL_RECTS[rect_id]['height']
    else:
        trigger_id = ctx.triggered[0]['prop_id'].split('.')[0]
        
        if trigger_id == 'interactive-plot' and relayout_data:
            
            # Rectangle was dragged in the plot
            
            shape_id = 1
            for k in relayout_data.keys():
                if 'shapes[' in k:
                    shape_id = int(k.split('[')[1].split(']')[0])
                        
            if shape_id not in range(1, NUM_SAMPLES + 1):
                raise PreventUpdate
                
                        
            angles = [0 for _ in range(NUM_SAMPLES)]
            rect_ids = [str(i + 1) for i in range(NUM_SAMPLES) if angles[i] == 0]
  
            rect_id = rect_ids[int(shape_id) - 1]
  
                
            if f'shapes[{shape_id}].x0' in relayout_data:
                x0 = round(relayout_data.get(f'shapes[{int(shape_id)}].x0', x0_input), 2)
                y0 = round(relayout_data.get(f'shapes[{int(shape_id)}].y0', y0_input), 2)
                x1 = round(relayout_data.get(f'shapes[{int(shape_id)}].x1', x1_input), 2)
                y1 = round(relayout_data.get(f'shapes[{int(shape_id)}].y1', y1_input), 2)
                
                # Calculate dimensions
                width = abs(x1 - x0)
                height = abs(y1 - y0)
                x = (x0 + x1) / 2
                y = (y0 + y1) / 2

            else:
                # Use current input values if no shape data
                x, y, width, height = x_input, y_input, width_input, height_input
        elif trigger_id == 'rect-id':
            x, y, width, height = rect_data[rect_id]['x'], rect_data[rect_id]['y'], rect_data[rect_id]['width'], rect_data[rect_id]['height']
        else:
            # Input boxes were changed
            x, y, width, height = x_input, y_input, width_input, height_input
    
    # Ensure values are not None
    rect_id = str(rect_id)
    x = x if x is not None else INITIAL_RECTS[rect_id]['x']
    y = y if y is not None else INITIAL_RECTS[rect_id]['y']
    width = width if width is not None else INITIAL_RECTS[rect_id]['width']
    height = height if height is not None else INITIAL_RECTS[rect_id]['height']
    
    rect_data[rect_id] = {'x':x,'y':y,'width':width,'height':height}
    for r, rect in rect_data.items():
        rect['width'] = width
        rect['height'] = height
    
    color_selection = {'width': '25px', 'height': '25px', 'border': '1px solid black',
                        'backgroundColor': SAMPLE_COLORS[rect_id],
                        'margin': '5px',
                        'display':'flex'}
    
    return x, y, width, height, rect_id, rect_data, color_selection

     
     

# -- update_plot (wafer platform) -----------------------------------------------
# Re-renders the wafer platform figure whenever rect-data changes.
@callback(
    Output('interactive-plot', 'figure'),
    Input('rect-data','data'),
    prevent_initial_call=False
)
def update_plot(rect_data):
    
    xes = [rect_data[i]['x'] for i in list(INITIAL_RECTS.keys()) ]
    yes = [rect_data[i]['y'] for i in list(INITIAL_RECTS.keys()) ]
    widths = [rect_data[i]['width'] for i in list(INITIAL_RECTS.keys()) ]
    heights = [rect_data[i]['height'] for i in list(INITIAL_RECTS.keys()) ]
    angles = [0 for _ in list(INITIAL_RECTS.keys())]
    
    fig = create_plot(xes, yes, widths, heights, angles)
    
    return(fig)


# ══════════════════════════════════════════════════════════════════════════════
# PROBE SELECTION UI
# ══════════════════════════════════════════════════════════════════════════════

# -- update_selection ----------------------------------------------------------
# Toggles probe selection when a probe card is clicked.  Maintains a list of
# up to 2 selected probes; clicking a third deselects the oldest.
@callback(
    Output("selected-probes", "data", allow_duplicate=True),
    Input({"type": "card-wrapper", "index": ALL}, "n_clicks"),
    State("selected-probes", "data"),
    prevent_initial_call=True
)
def update_selection(n_clicks, current_selection):
    # Ignore if no actual clicks have happened
    if not ctx.triggered_id or not any(n_clicks):
        return current_selection
    
    # Also check the specific triggered component was actually clicked
    triggered_clicks = ctx.triggered[0]["value"]
    if not triggered_clicks:
        return current_selection
    
    clicked_index = ctx.triggered_id["index"]
    new_selection = current_selection.copy()
    
    if clicked_index in new_selection:
        new_selection.remove(clicked_index)
    else:
        new_selection.append(clicked_index)
        if len(new_selection) > 2:
            new_selection.pop(0)
    
    return new_selection

# ─── PROBE CARD UI HELPERS ──────────────────────────────────────────────────────
# Probe count, labels, and colors are sourced from config.py.

def get_card_color(index, selected):
    """Return the background colour for a probe card based on selection state."""
    if index in selected:
        return PROBE_SELECTED_COLOR
    return PROBE_UNSELECTED_COLOR


def create_card(index, item, selected):
    """Build a single clickable probe card component."""
    is_selected = index in selected
    bg_color = get_card_color(index, selected)
    text_color = "white" if is_selected else "#333"

    return html.Div(
        dbc.Card(
            dbc.CardBody([
                html.Div([
                    html.Span(
                        item["label"],
                        style={
                            'fontSize': '12px',
                            "fontWeight": "bold" if is_selected else "normal",
                            "color": text_color
                        }
                    )
                ], style={
                    "display": "flex",
                    "alignItems": "center",
                    "justifyContent": "center"
                })
            ], className="p-0"),
            style={
                "width": "100px",
                "height": "20px",
                "backgroundColor": bg_color,
                "border": f"2px solid {PROBE_SELECTED_COLOR if is_selected else '#ced4da'}",
                "borderRadius": "6px"
            }
        ),
        id={"type": "card-wrapper", "index": index},
        n_clicks=0,
        className="rect-card-wrapper",
        style={}
    )

def create_rectangle_columns(selected):
    """
    Split probes into left/right columns for the probe selector UI.

    The probe list is sourced from ``PROBE_ITEMS`` in config.py, so adding a
    probe only requires updating ``NUM_PROBES`` there.
    """
    half = NUM_PROBES // 2
    left_items = [(i, PROBE_ITEMS[i]) for i in range(half)]
    right_items = [(i, PROBE_ITEMS[i]) for i in range(half, NUM_PROBES)]
    
    left_column = html.Div(
        [create_card(i, item, selected) for i, item in left_items],
        style={
            "display": "flex",
            "flexDirection": "column",
            "alignItems": "flex-end",
            "paddingRight": "95px",
            "height": "300px",  # Match the square height
            "justifyContent": "space-evenly",
            "paddingTop": "0px",
            "paddingBottom": "0px"
        }
    )
    
    right_column = html.Div(
        [create_card(i, item, selected) for i, item in right_items],
        style={
            "display": "flex",
            "flexDirection": "column",
            "alignItems": "flex-start",
            "paddingLeft": "15px",
            "height": "300px",  # Match the square height
            "justifyContent": "space-evenly",
            "paddingTop": "0px",
            "paddingBottom": "0px"
        }
    )
    
    return left_column, right_column

# -- update_cards --------------------------------------------------------------
# Re-renders the probe card grid whenever the selected-probes store changes.
@callback(
    Output("cards-container", "children"),
    Input("selected-probes", "data")
)
def update_cards(selected):
    left_column, right_column = create_rectangle_columns(selected)
    return [left_column, right_column]


# ══════════════════════════════════════════════════════════════════════════════
# UI PANEL CONTROLS
# ══════════════════════════════════════════════════════════════════════════════

# -- toggle_collapses ----------------------------------------------------------
# Handles the expand / collapse chevron buttons on each model output panel.
# Uses pattern-matching callbacks (ALL) so a single callback manages every
# collapsible section.
# Much cleaner callback
@callback(
    [Output({"type": "collapse", "index": ALL}, "is_open"),
     Output({"type": "collapse-button", "index": ALL}, "children")],
    [Input({"type": "collapse-button", "index": ALL}, "n_clicks")],
    [State({"type": "collapse", "index": ALL}, "is_open"),
     State({"type": "collapse", "index": ALL}, "id")],
    prevent_initial_call=True
)
def toggle_collapses(n_clicks_list, is_open_list, collapse_ids):
    if not ctx.triggered:
        return is_open_list, ["▼" if is_open else "▶" for is_open in is_open_list]
    
    # Get the triggered button's index
    triggered_index = ctx.triggered_id["index"]
    
    # Create new states
    new_is_open = []
    new_buttons = []
    
    for i, (is_open, collapse_id) in enumerate(zip(is_open_list, collapse_ids)):
        current_index = collapse_id["index"]
        
        if current_index == triggered_index:
            # Toggle this one
            new_state = not is_open
            new_is_open.append(new_state)
            new_buttons.append("▼" if new_state else "▶")
        else:
            # Keep the same state
            new_is_open.append(is_open)
            new_buttons.append("▼" if is_open else "▶")
    
    return new_is_open, new_buttons
