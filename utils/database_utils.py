"""
Database utilities for the RIE Digital Twin Dashboard.

Provides helpers for reading/writing simulation parameters and results
to the SQLite database.  All model-stage names and parameter lists are
sourced from ``config.py`` so that adding a model or parameter only
requires config changes.
"""

import sys
import sqlite3
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config import ALL_PARAMS, MODEL_ORDER, PARAM_SETS, DB_PATH, DATABASE_DROP_DUPLICATE_COLS


# ─── TABLE SCHEMAS ──────────────────────────────────────────────────────────────
# Each schema uses ``{table_name}`` as a placeholder filled at creation time.

plasma_schema = '''
    CREATE TABLE {table_name} (
        z FLOAT NOT NULL,
        r FLOAT NOT NULL,
        c_Cl FLOAT NOT NULL
    )
'''

flow_schema = '''
    CREATE TABLE {table_name} (
        x FLOAT NOT NULL,
        y FLOAT NOT NULL,
        z FLOAT NOT NULL,
        flux FLOAT NOT NULL,
        c_Cl FLOAT NOT NULL,
        v FLOAT NOT NULL
    )
'''

etch_schema = '''
    CREATE TABLE {table_name} (
        sample INT NOT NULL,
        x FLOAT NOT NULL,
        y FLOAT NOT NULL,
        etch_rate FLOAT NOT NULL
    )
'''

surface_schema = '''
    CREATE TABLE {table_name} (
        electrode INT NOT NULL,
        x FLOAT NOT NULL,
        y FLOAT NOT NULL,
        surface FLOAT NOT NULL
    )
'''

performance_schema = '''
    CREATE TABLE {table_name} (
        x FLOAT NOT NULL,
        y FLOAT NOT NULL,
        V FLOAT NOT NULL,
        I FLOAT NOT NULL,
        R FLOAT NOT NULL,
        V0 FLOAT NOT NULL,
        conductivity FLOAT NOT NULL
    )
'''

plasma_files_schema = '''
    CREATE TABLE {table_name} (
        c1n1x FLOAT NOT NULL,
        n_Cl FLOAT NOT NULL,
        n_Clplus FLOAT NOT NULL,
        n_Cl2plus FLOAT NOT NULL,
        Vr_Clplus FLOAT NOT NULL,
        Vr_Cl2plus FLOAT NOT NULL,
        Vz_Clplus FLOAT NOT NULL,
        Vz_Cl2plus FLOAT NOT NULL
    )
'''

flux_sample_schema = '''
    CREATE TABLE {table_name} (
        surf3x FLOAT,
        surf3y FLOAT,
        surf4x FLOAT,
        surf4y FLOAT,
        surf5x FLOAT,
        surf5y FLOAT,
        surf6x FLOAT,
        surf6y FLOAT,
        Color FLOAT NOT NULL
    )
'''


# ─── DERIVED COLUMN HELPERS ─────────────────────────────────────────────────────
# Built from the centralised ALL_PARAMS list so changes propagate automatically.

param_list = ALL_PARAMS
param_list_str = ','.join(param_list)
param_val_str = ','.join(['?'] * len(param_list))

# Model-stage status/table column names (generated from MODEL_ORDER)
_status_cols = [f'{m}_status' for m in MODEL_ORDER]
_table_cols = [f'{m}_table' for m in MODEL_ORDER]


# --- Parameter table schema ---
param_cols = ',\n        '.join([f'{p} FLOAT' if p not in ['probes'] else f'{p} TEXT' for p in param_list])  # Exclude 'probes' from FLOAT columns
table_cols = ',\n        '.join([f'{m}_table TEXT' for m in MODEL_ORDER])
status_cols = ',\n        '.join([f'{m}_status INTEGER' for m in MODEL_ORDER])
duration_cols = ',\n        '.join([f'{m}_duration FLOAT' for m in MODEL_ORDER])

params_schema = f'''
    CREATE TABLE IF NOT EXISTS parameters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        status TEXT,
        roughness FLOAT,
        nonuniformity FLOAT,
        conductance FLOAT,
        effective_conductivity FLOAT,
        {param_cols},
        {status_cols},
        {table_cols},
        {duration_cols},
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
'''

# ─── QUERY HELPERS ──────────────────────────────────────────────────────────────

def get_where_clause(param_set, parameter_data):
    """
    Build a parameterised WHERE clause matching *param_set* values.

    Returns ``(sql, values)`` suitable for ``cursor.execute(sql, values)``.
    """
    where_clauses = []
    values = []
    for param in param_set:
        where_clauses.append(f"{param} = ?")
        values.append(parameter_data[param])
    where_sql = " AND ".join(where_clauses)
    sql = f"SELECT * FROM parameters WHERE {where_sql} LIMIT 1"
    return sql, values


def check_db(parameter_data, param_set, model):
    """
    Check whether a result already exists in the DB for the given parameters
    and model stage.

    Returns ``(status, table_name_or_None, row_id_or_None)``.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        sql, values = get_where_clause(param_set, parameter_data)
        cursor.execute(sql, values)
        result = cursor.fetchone()

    if result:
        result = dict(result)
        if result[f'{model}_status'] == 1:
            return result[f'{model}_status'], result[f'{model}_table'], result['id']
        return result[f'{model}_status'], None, result['id']
    return 0, None, None


# ─── PARAMETER INSERTION ────────────────────────────────────────────────────────

def add_params_to_db(new_params, run_model):
    """
    Insert a new parameter row for *run_model* and carry forward any
    previously-computed model tables.

    Instead of a separate INSERT branch per model, this function walks
    ``MODEL_ORDER`` to:
      1. Look up which upstream model tables already exist.
      2. Set ``<model>_status`` to 1 for all completed predecessors.
      3. Populate parameter values (``None`` for params not yet known).

    Returns the new row id.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Determine the index of the current model in the pipeline
        model_idx = MODEL_ORDER.index(run_model)

        # --- Carry forward upstream model tables ---
        prior_tables = {m: None for m in MODEL_ORDER}
        if model_idx > 0:
            # Look up the row that matches all params of the *previous* model
            prev_model = MODEL_ORDER[model_idx - 1]
            prev_params = PARAM_SETS[prev_model]
            sql, vals = get_where_clause(prev_params, new_params)
            cursor.execute(sql, vals)
            row = cursor.fetchone()
            if row:
                row = dict(row)
                for m in MODEL_ORDER[:model_idx]:
                    prior_tables[m] = row.get(f'{m}_table')

        # --- Build column value list for param_list ---
        param_values = []
        # Parameters that belong to the current model's param set get their
        # real value; others default to None (not yet determined).
        current_param_set = set(PARAM_SETS[run_model])
        for p in param_list:
            if p in current_param_set:
                param_values.append(new_params.get(p))
            else:
                param_values.append(None)

        # --- Status flags: 1 for completed predecessors, 0 otherwise ---
        status_values = [1 if i < model_idx else 0 for i in range(len(MODEL_ORDER))]

        # --- Table references ---
        table_values = [prior_tables[m] for m in MODEL_ORDER]

        status_label = f'{run_model.capitalize()} Started'

        all_values = (
            [status_label] + param_values + status_values + table_values
        )

        status_col_str = ', '.join(_status_cols)
        table_col_str = ', '.join(_table_cols)
        placeholders = ', '.join(['?'] * len(all_values))

        cursor.execute(
            f"INSERT INTO parameters "
            f"(status, {param_list_str}, {status_col_str}, {table_col_str}) "
            f"VALUES ({placeholders})",
            all_values,
        )

        new_row_id = cursor.lastrowid
        conn.commit()

    print(f"✅ Added {new_row_id} to Parameters")
    return new_row_id


# ─── ROW UPDATE HELPERS ─────────────────────────────────────────────────────────

def update_param_value(row_id, param_name, param_value):
    """Update a single column for a given row in the parameters table."""
    print(f"Updating row {row_id}: setting {param_name} to {param_value}")
    # Convert numpy types to native Python types
    if hasattr(param_value, 'item'):
        param_value = param_value.item()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            f'UPDATE parameters SET {param_name} = ? WHERE id = ?',
            (param_value, row_id),
        )
        conn.commit()
    print(f"✅ Updated {param_name} to {param_value}")


def get_param_value(row_id, param_name):
    """Read a single column value for a given row."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT {param_name} FROM parameters WHERE id = ?',
            (row_id,),
        )
        result = cursor.fetchone()
    return result[param_name] if result else None


def update_model_status(row_id, model, table_name):
    """Mark a model stage as completed and record its result table name."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            f'UPDATE parameters SET {model}_status = ? WHERE id = ?',
            (1, row_id),
        )
        cursor.execute(
            f'UPDATE parameters SET {model}_table = ? WHERE id = ?',
            (table_name, row_id),
        )
        conn.commit()
    print(f"✅ Updated {model} status")


# ─── DATA LOADING ───────────────────────────────────────────────────────────────

def load_data(table):
    """Load an entire table into a DataFrame."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(f'SELECT * FROM {table}', conn)
    return df
    
def get_matching_tables(conn, pattern_prefix):
    """
    Get tables matching a pattern like 'user_*' where * is a number
    """
    cursor = conn.cursor()
    
    # Get all table names
    cursor.execute('''
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
    ''')
    
    all_tables = [row[0] for row in cursor.fetchall()]
    
    # Filter tables matching the pattern
    matching_tables = []
    numbers = []
    
    for table in all_tables:
        if table.startswith(pattern_prefix):
            # Extract the number part
            suffix = table[len(pattern_prefix):]
            if suffix.isdigit():
                matching_tables.append(table)
                numbers.append(int(suffix))
    
    return matching_tables, numbers
    
def create_next_table_and_fill(db_path, pattern_prefix, table_schema, data, row_id):
    """
    Create a table with the next sequential number
    
    Parameters:
    db_path: path to SQLite database
    pattern_prefix: prefix like 'user_'
    table_schema: SQL schema for the new table
    row_id: Row of the Parameter table to update status of
    """
    
    with sqlite3.connect(db_path) as conn:
        # Get matching tables and their numbers
        matching_tables, numbers = get_matching_tables(conn, pattern_prefix)
        
        print(f"Found tables: {matching_tables}")
        
        # Determine next number
        if numbers:
            next_number = max(numbers) + 1
        else:
            next_number = 1
        
        # Create new table name
        new_table_name = f"{pattern_prefix}{next_number}"
        
        # Create the table
        create_sql = table_schema.format(table_name=new_table_name)

        conn.execute(create_sql)
        conn.commit()
        
        print(f"✅ Created new table: {new_table_name}")
        
        data.to_sql(new_table_name, conn, if_exists='replace', index=False)
        
        update_model_status(row_id, pattern_prefix.strip('_'), new_table_name)
    
    return new_table_name
        
    #except Exception as e:
    #    print(f"❌ Error: {e}")
    #    return None
    
    #finally:
    #    conn.close()
    
def create_table_and_fill(db_path, new_table_name, table_schema, data):
    """
    Create a table with the next sequential number
    
    Parameters:
    db_path: path to SQLite database
    new_table_name: name of the new table
    table_schema: SQL schema for the new table
    data: dataframe to add
    """
    
    with sqlite3.connect(db_path) as conn:
 
        # Create the table
        create_sql = table_schema.format(table_name=new_table_name)

        conn.execute(create_sql)
        conn.commit()
        
        print(f"✅ Created new table: {new_table_name}")
        
        data.to_sql(new_table_name, conn, if_exists='replace', index=False)
            
    return new_table_name


def drop_redundant_nans(df):
    """
    Drop rows with NaNs only if there's another row that matches all non-NaN values
    and has actual values where the current row has NaNs.
    """
    # Create a copy to avoid modifying original
    df = df.copy()
    
    # Track rows to drop
    rows_to_drop = []
    
    for idx in df.index:
        row = df.loc[idx]
        
        # Skip if row has no NaNs
        if not row.isna().any():
            continue
        
        # Get columns that are NaN in this row
        nan_cols = row[row.isna()].index.tolist()
        
        # Get columns that are NOT NaN in this row
        non_nan_cols = row[~row.isna()].index.tolist()
        non_nan_cols = [c for c in non_nan_cols if c in DATABASE_DROP_DUPLICATE_COLS]  # Exclude columns from matching criteria
        
        # Look for matching rows
        mask = pd.Series([True] * len(df), index=df.index)
        
        # Match on all non-NaN columns
        for col in non_nan_cols:
            mask &= (df[col] == row[col]) | (df[col].isna() & pd.isna(row[col]))
        
        # Exclude the current row itself
        mask[idx] = False
        
        # Check if any matching row has values in the NaN columns
        matching_rows = df[mask]
        
        if len(matching_rows) > 0:
            # Check if at least one matching row has non-NaN values in the NaN columns
            has_better_match = matching_rows[nan_cols].notna().any(axis=1).any()
            
            if has_better_match:
                rows_to_drop.append(idx)
    
    # Drop the identified rows
    return df.drop(rows_to_drop)

def create_database(db_path):
    """
    Create the database and the parameters table if it doesn't exist.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(params_schema)
        conn.commit()
    print("✅ Database and parameters table created (if not already present).")
    

def clear_database_and_recreate(db_path):
    """
    Clear all tables in the database.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        for table_name in tables:
            if table_name[0] != 'sqlite_sequence':
                table_name = table_name[0]
                cursor.execute(f"DROP TABLE IF EXISTS {table_name};")
        conn.commit()
    print("✅ Cleared all tables in the database.")
    create_database(db_path)
    
    