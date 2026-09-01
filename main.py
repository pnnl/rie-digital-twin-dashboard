import dash
import dash_bootstrap_components as dbc
from components.layout import create_layout
from callbacks import interactions  # This registers the callbacks
from config import APP_TITLE

# Initialize the Dash app
app = dash.Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
app.title = APP_TITLE

# Custom CSS for hover effects
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%css%}
        <style>
            .rect-card-wrapper {
                transition: all 0.2s ease;
                cursor: pointer;
            }
            .rect-card-wrapper:hover {
                transform: translateX(3px);
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            }
            .base-square {
                background: linear-gradient(145deg, #6c757d, #495057);
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''

# Set layout
app.layout = create_layout()

if __name__ == '__main__':
    app.run(debug=True)