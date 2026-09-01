import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.cm import ScalarMappable
from scipy.interpolate import interp1d
import os
from matplotlib.colors import Normalize
from matplotlib import colors
import scipy.interpolate as interp
import glob
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from utils.params import *

import argparse

parser = argparse.ArgumentParser()
parser.add_argument('log_file', type=str)
parser.add_argument('t_max', type=float, default=15.0)
parser.add_argument('radii', nargs=4, default=[0.0,0.045,0.09,0.09])


args = parser.parse_args()

#Input
log_file = args.log_file
t_max = args.t_max
radii = args.radii

print('radii = ', radii)

plt.rcParams.update({
    'font.size': 15,
    'font.family': 'Times New Roman'
})


################# Physical Etching rate Calculation ################
def load_and_interpolate_physical(r):
    """
    Load and interpolate values for specific variables at a given arc length (r).

    Parameters:
        folder_path (str): Path to the folder containing the .txt files.
        r (float): Arc length for which the values should be interpolated.
        variable_names (list): List of variables to extract (e.g., n_Cl, n_Cl+, Vr_Cl+, etc.).

    Returns:
        dict: A dictionary with variable names as keys and interpolated values as values.
    """

    def data_interpolation(filename, r):
        # Skip headers and load the relevant numerical data
        data_lines = []
        with open(filename, 'r') as file:
            for line in file:
                if not line.startswith('%'):  # Skip lines starting with '%'
                    data_lines.append(line.strip())

        # Parse the data into two lists: arc length and output
        arc_length = []
        output = []
        for line in data_lines:
            values = line.split()
            if len(values) == 2:
                arc_length.append(float(values[0]))
                output.append(float(values[1]))

        # Create the interpolation function based on the loaded data
        interp_function = interp1d(
            np.array(arc_length), np.array(output), kind='linear', fill_value="extrapolate"
        )


        # Interpolate at the specified point
        return interp_function(r)

    # Dictionary to store results for the requested variables
    interpolated_values = {}
    folder_path = "outputs"  # Path to the folder with .txt files
    variable_names = ["n_Cl", "n_Cl+", "n_Cl2+", "Vr_Cl+", "Vr_Cl2+", "Vz_Cl+", "Vz_Cl2+"]  # Variables to extract

    # Automatically process all .txt files in the folder
    for filename in os.listdir(folder_path):
        if filename.endswith(".txt"):  # Ensure only .txt files are processed
            # Extract the core variable name from the filename
            for var in variable_names:
                if var in filename:  # Match the variable name with its filename
                    full_path = os.path.join(folder_path, filename)
                    interpolated_value = data_interpolation(full_path, r)
                    interpolated_values[var] = interpolated_value

    n_Cl = interpolated_values['n_Cl'] # [1/m3]
    n_Cl_p = interpolated_values['n_Cl+'] # [1/m3]
    n_Cl2_p = interpolated_values['n_Cl2+'] # [1/m3]
    Vr_Cl_p = interpolated_values['Vr_Cl+'] # [m/s]
    Vz_Cl_p = interpolated_values['Vz_Cl+']
    Vr_Cl2_p = interpolated_values['Vr_Cl2+']
    Vz_Cl2_p = interpolated_values['Vz_Cl2+']
    
    return n_Cl, n_Cl_p, n_Cl2_p, Vr_Cl_p, Vr_Cl2_p, Vz_Cl_p, Vz_Cl2_p

def EDL_acceleration(Vz_Cl_p, Vz_Cl2_p, n_Cl_p, n_Cl2_p):
    #### EDL acceleration
    m = 5.81e-26
    epsilon_0 = 8.854e-12  # Permittivity of free space in F/m
    k = 1.381e-23          # Boltzmann constant in J/K
    q = 1.6e-19 
    debye_length = np.sqrt((epsilon_0 * k * 5e8) / ((n_Cl_p + n_Cl2_p*2.0) * q**2))
    # Constants
    lambda_D = np.mean(debye_length)  # Debye length in meters (example value)
    ns_e = np.mean(1.2e14*lambda_D*7)  # Electron density in 1/m^3 (example value)
    e = 1.6e-19  # Elementary charge in coulombs
    epsilon_0 = 8.85e-12  # Vacuum permittivity in F/m
    # Distance range
    d = np.arange(0, 10e-3, 1e-5)  # Create an array of distances from 0 to 1e-5 with step 2e-3
    # Calculate potential
    phi = (ns_e * e * lambda_D / epsilon_0) * np.exp(-d / lambda_D)
    E1 = -(phi[-1]-phi[0])/(d[-1]-d[0])
    I1 = E1*q*d
    Vz_Cl_p_EDL = np.sqrt(Vz_Cl_p**2 + 2*I1/m)[-1]
    Vz_Cl2_p_EDL = np.sqrt(Vz_Cl2_p**2 + 2*I1/m/2.0)[-1]
    return Vz_Cl_p_EDL, Vz_Cl2_p_EDL

# Function to calculate the etch rate (same as provided earlier)
def calculate_physical_etch_rate_ion(m_ion, v_ion, n_ion):
    # Parameters for the etching calculation
    k_ion = 131e14      # Ion etching rate constant (unit)
    density_Al = 2.70  # Density of aluminum (g/cm^3)
    atomic_mass_Al = 26.98  # Atomic mass of Al (g/mol)
    avogadro_number = 6.022e23  # Avogadro's number (atoms/mol)
    E = 0.5 * m_ion * v_ion**2  # Ion energy (E = 0.5 * m * v^2)
    # Convert density from g/cm^3 to kg/m^3
    density_Al_kg_m3 = density_Al * 1e3  # (kg/m^3)

    # Calculate atomic density of Al in atoms per cubic meter (atoms/m^3)
    rho_a = (density_Al_kg_m3 * avogadro_number) / atomic_mass_Al

    # Calculate the threshold energy at the given reference velocity
    v_ref = 4000  # Reference velocity (m/s)
    E_th = 0.5 * v_ref**2 * m_ion  # Threshold energy (J)
    

    if E < E_th:
        raise ValueError("Ion energy (E) must be greater than or equal to threshold energy (E_th).")
    J_i = n_ion * v_ion  # Ion flux (J_i = n_ion * v_ion)
    ER_ion = (k_ion * J_i * (np.sqrt(E) - np.sqrt(E_th))) / rho_a /60

    return ER_ion

def calculate_physical_etch_rate(r):
    m_Cl2_ion = 5.9e-26   # Ion mass of Cl2 (kg)
    m_Cl_ion = m_Cl2_ion/2.0
    n_Cl, n_Cl_p, n_Cl2_p, Vr_Cl_p, Vr_Cl2_p, Vz_Cl_p, Vz_Cl2_p = load_and_interpolate_physical(r)
    Vz_Cl_p_EDL, Vz_Cl2_p_EDL = EDL_acceleration(Vz_Cl_p, Vz_Cl2_p, n_Cl_p, n_Cl2_p)    

    etch_rates_Cl2_p = calculate_physical_etch_rate_ion(m_Cl2_ion, Vz_Cl2_p_EDL, n_Cl2_p) # nm/min
    etch_rates_Cl_p = calculate_physical_etch_rate_ion(m_Cl_ion, Vz_Cl_p_EDL, n_Cl_p)
    Etch_rate_Physical = etch_rates_Cl_p + etch_rates_Cl2_p

    return Etch_rate_Physical



################### Chemical Etching rate load #######################
def load_and_interpolate_chemical(samplename, Size_x, Size_y):
    # Load the data skipping the header lines (lines beginning with '%')
    print(samplename)
    filename = 'outputs/' + samplename + '.txt'
    with open(filename, 'r') as f:
        # Skip lines starting with '%'
        content = [line for line in f if not line.startswith('%')]

    print(content[0])
    # Convert the filtered content to a numpy array
    data = np.loadtxt(content)

    # Extract columns
    x = data[:, 0]  # First column: X-coordinates
    y = data[:, 1]  # Second column: Y-coordinates
    f = data[:, 2]      # Third column: Flux values (Color)

    x_coords = x-np.min(x)  # x-coordinates
    y_coords = y-np.min(y)  # y-coordinates
    flux = f      # flux (Color)

    grid_size = 1e-4 #[m]


    # Create a grid over the data range
    grid_x, grid_y = np.meshgrid(
        np.linspace(0.0, Size_x, int(Size_x/grid_size)+1),  # Adjust resolution as needed
        np.linspace(0.0, Size_y, int(Size_y/grid_size)+1)
    )

    # Interpolate the scattered flux values onto the uniform grid
    grid_flux = interp.griddata(
        (x_coords, y_coords), flux, (grid_x, grid_y), method='linear'
    )


    # plt.figure(figsize=(6, 5))

    # # Create filled contour
    # contour = plt.contourf(grid_x, grid_y, grid_flux, levels=100, cmap='jet', vmin=0e-5, vmax=4e-5)

    # # Create colorbar (capture its handle)
    # cbar = plt.colorbar(contour)
    # cbar.set_label("Flux")

    # # plt.title(samplename)
    # plt.xlabel("X")
    # plt.ylabel("Y")
    # plt.show()

    return grid_flux*1.0e6



############# Visualization #######################

    
def create_dual_colormap(Z, thick_sample, thick_metal):
    """
    Create facecolors for z_values using dual colormaps.
    - z <= split_value is colored using "Oranges".
    - z > split_value is colored using "Blues".
    """

    # Norms for each colormap
    norm_low = Normalize(vmin=(thick_sample-thick_metal)*0.8, vmax=thick_sample-thick_metal)         # Orange colormap (0 to 200)
    norm_high = Normalize(vmin=thick_sample-thick_metal, vmax=thick_sample)      # Blue colormap (200 to 400)

    # Colormaps
    cmap_low = plt.get_cmap("Oranges")
    cmap_high = plt.get_cmap("Blues")

   # Vectorized logic for facecolors
    quad_avg = Z[:-1, :-1]
    facecolors = np.empty((quad_avg.shape[0], quad_avg.shape[1], 4), dtype=float)
    
    # Apply orange colormap for Z ≤ split_value
    mask_low = quad_avg <= thick_sample-thick_metal
    facecolors[mask_low] = cmap_low(norm_low(quad_avg[mask_low]))

    # Apply blue colormap for Z > split_value
    mask_high = ~mask_low
    facecolors[mask_high] = cmap_high(norm_high(quad_avg[mask_high]))

    return facecolors


def update_frame(i, times, thick_sample, thick_metal, nx, ny, rate, rate_ratio_lower, dt, Z_bot, XI, YI, xi, yi, boundary_coords, ax):
    global top_surf, filled_side_surfs, TI_surface, outline_line, Z_top, flag_cover, facecolors
    
    t = times[i]
    # Remove previous animated surfaces and outline
    top_surf.remove()
    for surf in filled_side_surfs:
        surf.remove()
    if outline_line:
        outline_line.remove()
    filled_side_surfs.clear()
    # Compute new top
    # Time increment for this frame
    t_prev = times[i - 1] if i > 0 else 0
    t_curr = times[i]
    nsteps = int((t_curr - t_prev) / dt)
    sigma0 = 12.7 # [nm] surface roughness calibration

    print('Nsteps:', nsteps, 't_curr:', t_curr, 't_prev:', t_prev)

    # Integrate with dt so the rate switches exactly when crossing the interface
    for _ in range(nsteps):
        eff = rate +   (np.random.rand(ny, nx)*2 - 1)*sigma0/np.sqrt((times[-1]-times[0])/dt)           # base (upper-layer) rate
        eff[Z_top <= thick_sample-thick_metal] *= rate_ratio_lower  # slow in lower layer
        Z_top = np.maximum(Z_top - eff * dt, Z_bot)    # do not go below bottom
    
    # Z_top = np.round(Z_top, 3) ### rounded to the nm level
    # Plot updated top
    facecolors = create_dual_colormap(Z_top, thick_sample, thick_metal)
    flag_cover = rate*0.0
    flag_cover[75:80, :20] = 1.0
    flag_cover[75:80, -20:] = 1.0
    flag_cover[50:55, :20] = 1.0
    flag_cover[50:55, -20:] = 1.0
    flag_cover[25:31, :20] = 1.0
    flag_cover[25:31, -20:] = 1.0
    Z_top_cover = Z_top*(1.0-flag_cover) +flag_cover*thick_sample
    
    top_surf = ax.plot_surface(
        XI*1000, YI*1000, Z_top_cover, facecolors=facecolors,alpha=1.0,
        linewidth=0.0,  antialiased=False)

    # Plot filled side walls
    for Xw, Yw, Zw in side_wall_coords(Z_top_cover, xi*1000, yi*1000, Z_bot, ny):
        surf = ax.plot_surface(Xw, Yw, Zw,
                               color='lightgray', alpha=0.6,
                               linewidth=0, antialiased=True)
        filled_side_surfs.append(surf)
    # Re-draw outline on top
    outline_line, = ax.plot(boundary_coords[:,0],
                             boundary_coords[:,1],
                             boundary_coords[:,2],
                             color='k', linewidth=1)
    ax.set_title(f"t = {t} s")
    plt.tight_layout()

    # save the data
    if t == times[-1]:
        NPr = np.zeros(Z_top.shape)
        NPr[75, :20] = 1
        NPr[75, -20:] = 2
        NPr[50, :20] = 3
        NPr[50, -20:] = 4
        NPr[25, :20] = 5
        NPr[25, -20:] = 6

        grid_X = XI*100 + 0.5
        grid_Y = YI*100 + 0.5
        H = Z_top * 1.0
        H[NPr>0.5] = thick_sample
        data_save = np.column_stack((NPr.ravel(),grid_X.ravel(), grid_Y.ravel(), H.ravel()))
        np.savetxt('data_100_1cm.txt', data_save, fmt=['%d','%.8e','%.8e','%.8e'], delimiter='\t', 
                header='Electrode #\tX\tY\tFlux', comments='')
        
    return 

def step_surface(i, times, thick_sample, thick_metal, nx, ny, rate, rate_ratio_lower, dt, Z_bot, XI, YI, sample):
    global top_surf, filled_side_surfs, TI_surface, outline_line, Z_top, flag_cover, facecolors
    
    t = times[i]
   
    # Compute new top
    # Time increment for this frame
    t_prev = times[i - 1] if i > 0 else 0
    t_curr = times[i]
    nsteps = int((t_curr - t_prev) / dt)
    sigma0 = 12.7 # [nm] surface roughness calibration

    # Integrate with dt so the rate switches exactly when crossing the interface
    for _ in range(nsteps):
        eff = rate +   (np.random.rand(ny, nx)*2 - 1)*sigma0/np.sqrt(200.0/dt)           # base (upper-layer) rate
        eff[Z_top <= thick_sample-thick_metal] *= rate_ratio_lower  # slow in lower layer
        Z_top = np.maximum(Z_top - eff * dt, Z_bot)    # do not go below bottom
    
    #print(f'Sample = {sample}, t = {t}, Z_top={np.nanmin(Z_top)}-{np.nanmax(Z_top)}')
    # Plot updated top
    flag_cover = rate*0.0
    flag_cover[75:80, :20] = 1.0
    flag_cover[75:80, -20:] = 1.0
    flag_cover[50:55, :20] = 1.0
    flag_cover[50:55, -20:] = 1.0
    flag_cover[25:31, :20] = 1.0
    flag_cover[25:31, -20:] = 1.0
    #Z_top_cover = Z_top*(1.0-flag_cover) +flag_cover*thick_sample
    
    # save the data
    if t == times[-1]:
        NPr = np.zeros(Z_top.shape)
        NPr[75, :20] = 1
        NPr[75, -20:] = 2
        NPr[50, :20] = 3
        NPr[50, -20:] = 4
        NPr[25, :20] = 5
        NPr[25, -20:] = 6

        grid_X = XI*100 + 0.5
        grid_Y = YI*100 + 0.5
        H = Z_top * 1.0
        H[NPr>0.5] = thick_sample
        data_save = np.column_stack((NPr.ravel(),grid_X.ravel(), grid_Y.ravel(), H.ravel()))
        np.savetxt(f'outputs/etching_final_surface_{sample}.txt', data_save, fmt=['%d','%.8e','%.8e','%.8e'], delimiter='\t', 
                header='Electrode #\tX\tY\tFlux', comments='')
        
    return 

def get_final_surface(rate, sample, times):
    global top_surf, filled_side_surfs, outline_line, Z_top, facecolors, TI_surface, flag_cover
    
    height_mean = thick_sample
    Z_top = height_mean + (np.random.rand(ny, nx)*2 - 1) * noise_amp *0.0
    
    # Bottom surface (flat)
    Z_bot = np.zeros_like(XI)
    
    #times = np.arange(0, T_max+1, save_interval)
        
    for i,t in enumerate(times):
        step_surface(i,times, thick_sample, thick_metal, nx, ny, rate, rate_ratio_lower, dt, Z_bot, XI, YI, sample)

def Etching_visualization(length_x, length_y, rate, times):
    global top_surf, filled_side_surfs, outline_line, Z_top, facecolors, TI_surface, flag_cover
    
    # consider photoresist area

    # Derived
    height_mean = thick_sample

    # Bottom surface (flat)
    Z_bot = np.zeros_like(XI)

    # Initial noisy top surface
    Z_top = height_mean + (np.random.rand(ny, nx)*2 - 1) * noise_amp *0.0

    # Compute initial boundary coords (row-major perimeter)
    boundary = []
    boundary += [0*nx + i for i in range(nx)]
    boundary += [j*nx + (nx-1) for j in range(1, ny)]
    boundary += [(ny-1)*nx + i for i in range(nx-2, -1, -1)]
    boundary += [j*nx + 0 for j in range(ny-2, 0, -1)]
    pts_top0 = np.c_[XI.ravel(), YI.ravel(), Z_top.ravel()]
    boundary_coords = pts_top0[boundary]


    # Set up figure and axes
    fig = plt.figure(figsize=(8, 5))
    ax  = fig.add_subplot(111, projection='3d')

    # Build initial facecolors per quad (classify by average of 4 corners vs. interface_z)
    facecolors = create_dual_colormap(Z_top, thick_sample, thick_metal)

    # Plot static bottom
    ax.plot_surface(XI*1000, YI*1000, Z_bot,
                    color='lightgray', alpha=0.5, linewidth=0, antialiased=False)

    TI_surface = ax.plot_surface(XI*1000, YI*1000, Z_bot+thick_sample-thick_metal,
                    color='red', alpha=0.5, linewidth=0, antialiased=False)

    # Placeholders for animated and outline objects
    top_surf = ax.plot_surface(XI*1000, YI*1000, Z_top,
                            cmap='Blues', alpha=0.8,norm=Normalize(vmin=0, vmax=thick_sample),
                            linewidth=0.2, edgecolor='k', antialiased=True)

    filled_side_surfs = []
    outline_line = None

    # Axis settings
    ax.set_xlim(-length_x/2*1000, length_x/2*1000)
    ax.set_ylim(-length_y/2*1000, length_y/2*1000)
    ax.set_zlim(0, height_mean + noise_amp + 0.5)
    ax.set_box_aspect((1, 1, 0.3))
    ax.set_xlabel('x [mm]')
    ax.set_ylabel('y [mm]')
    ax.set_zlabel('z [nm]')

    # # Add colorbar

    mappable = ScalarMappable(cmap='Blues')
    mappable.set_array(Z_top)
    cbar = fig.colorbar(mappable, ax=ax, shrink=0.6, pad=0.1)
    cbar.set_label("Top Surface Height")
    mappable.set_clim(100, 400)
    cbar.update_normal(mappable)

    # # Create and save GIF
    ani = FuncAnimation(fig, update_frame, frames=len(times), interval=500, fargs=(times, thick_sample, thick_metal, nx, ny, rate, rate_ratio_lower, dt, Z_bot, XI, YI, xi, yi, boundary_coords, ax))
    writer = PillowWriter(fps=3)
    #ani.save("output_small.gif", writer=writer)
    plt.close(fig)

    
if __name__ == "__main__":
    ##################### Main program #################
    ### !!!!!!!!!! This python script load data from flow model (saved in outputs/'Flux_sample#_wor.txt') and plasma generation model (saved in outputs/n_Cl and n_Cl+ and n_Cl2+ and Vr and Vz)
    ### !!!!!!!!!! This script finally generate animation defined in line 412, and save data for performance model in line 323.

    # Control parameters 
    length_x = 1e-2    # X dimension in m
    length_y = 1e-2    # Y dimension in m
    #Radial_distrace_sample = 0.05 # [m] Radiam distance of sample from the wafer center

    save_interval = t_max / 50.0
    times = np.arange(0, t_max+save_interval, save_interval)

    with open(args.log_file, 'w') as f:
            f.write(f'           Current Progress:   0% - Starting\n')

    rates = []
    for i in range(1,5):    
        samplename = f'Flux_sample{i}_wor' # sample 1 = center, sample 2 = center mid, sample 3 = center edge, sample 4 = forward edge
        Radial_distrace_sample = radii[i-1]

        print(i,Radial_distrace_sample)

        with open(args.log_file, 'w') as f:
                f.write(f'           Current Progress:   {10 + 10*(i+1)}% - Loading sample {i}\n')

        # Etching rate calculation
        Etch_rate_Chemical1 = load_and_interpolate_chemical(samplename, length_x, length_y) # Calculate Chemical etching rate
        
        
        with open(args.log_file, 'w') as f:
            f.write(f'           Current Progress:   {20 + 10*(i+1)}% - Calculating physical etch rate for sample {i}\n')
        
        Etch_rate_Physical = calculate_physical_etch_rate(Radial_distrace_sample) ### Physical etching rate
        
        Etch_rate_Chemical1 = Etch_rate_Chemical1*(1.0/3.64) # correcting calibration due to difference in Cl concentration
        rate = (Etch_rate_Chemical1 + Etch_rate_Physical)/60       # [nm/sec] Total etching rate involving chemical and physical
        rates.append(rate)
        
        print('Chemical:',i,np.mean(Etch_rate_Chemical1), np.min(Etch_rate_Chemical1), np.max(Etch_rate_Chemical1))
        print('Physical:',i,np.mean(Etch_rate_Physical), np.min(Etch_rate_Physical), np.max(Etch_rate_Physical))
        print('Total:',i,np.mean(rate), np.min(rate), np.max(rate))
        
        get_final_surface(rate, i, times)

        #with open(args.log_file, 'w') as f:
        #    f.write(f'           Current Progress:   75% - Visualizing\n')

        # Visualization and generate data
        #Etching_visualization(length_x, length_y, rate)
        
        
    with open(args.log_file, 'w') as f:
        f.write(f'           Current Progress:   90% - Saving results\n')
        
    rate = np.stack(rates,axis=0)
    rate[rate == None] = 0
    print('Saving etch_output_rate.npy')
    np.save('outputs/etch_output_rate.npy', rate)
    
    with open(args.log_file, 'w') as f:
        f.write(f'           Current Progress:   100% - Done\n')