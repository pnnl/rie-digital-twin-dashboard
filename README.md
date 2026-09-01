# Digital Twin of RIE Dashboard

An interactive web dashboard built with [Plotly Dash](https://dash.plotly.com/) that serves as a digital twin for a Reactive Ion Etching (RIE) plasma process. The dashboard allows users to visualize simulation outputs, configure process parameters (temperature, pressure, power, frequency, gas flow, etc.), and monitor etching results across multiple sample locations.

## Project Structure

```
dashboard/
├── main.py              # App entry point
├── requirements.txt     # Python dependencies
├── components/
│   └── layout.py        # UI layout and component definitions
├── callbacks/
│   └── interactions.py  # Dash callback logic
├── database/            # Database storage
├── models/              # Simulation models
├── utils/               # Shared helper utilities
├── outputs/             # Generated model output files
└── logs/                # Application logs
```

## Installation

1. **Create and activate a conda environment** (recommended):
   ```bash
   conda create -n dashboard python=3.12
   conda activate dashboard
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Alternative setup for Apple Silicon Macs**
   
   This project uses `mph` to interface with COMSOL 5.5, which ships only with an Intel (x86_64) JVM. On Apple Silicon Macs (M1/M2/M3/M4), the Python environment must therefore also be x86_64 so that JPype can load COMSOL's JVM.
   
   Create an x86_64 conda environment so it matches COMSOL 5.5's Intel JVM:
   ```
   CONDA_SUBDIR=osx-64 conda create -n dashboard python=3.12
   conda activate dashboard
   conda config --env --set subdir osx-64   # pin env to x86_64
   pip install -r requirements.txt
   ```
   Verify the environment is x86_64:
   ```
   python -c "import platform; print(platform.machine())"
   ```
   Expected output: x86_64

## Running the Dashboard

From the `dashboard/` directory, run:

```bash
python main.py
```

The app will start in debug mode and be available at [http://127.0.0.1:8050](http://127.0.0.1:8050) by default.

To run in production mode (debug disabled), edit the last line of `main.py`:
```python
app.run(debug=False)
```

# Acknowledgements

This work was supported by the Adaptive Tunability for Synthesis and Control via Autonomous Learning on Edge (AT SCALE), which is a Laboratory Directed Research and Development Project at Pacific Northwest National Laboratory (PNNL). PNNL is a multiprogram national laboratory operated for the U.S. Department of Energy (DOE) by Battelle Memorial Institute under Contract no. DE-AC05-76RL01830.