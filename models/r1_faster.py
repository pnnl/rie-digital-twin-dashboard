import mph
import numpy as np
import os
import jpype
from argparse import ArgumentParser
from scipy.ndimage import distance_transform_edt


parser = ArgumentParser()
parser.add_argument('probes',default='1-4')
parser.add_argument('log_file')

args = parser.parse_args()

def log_status(status, percent):
    status = f'           Current Progress:   {percent}% - {status}\n'
    with open(args.log_file, 'a') as f:
        f.write(status)

#Input
probes = args.probes
log_file = args.log_file

# Initialize COMSOL client
#client = mph.Client()
client = mph.start()


#output_file = "output.txt"
skip_value1 = 0.00000000e+00
#skip_value2 = 1.00000000e-02
skip_value2 = 5.00000000e-01
block_size = 1 #(10,100) (5,200),(2,500),(1,1000), (1,100)
inputdomain =  100


locs = [4171, 4121, 4071, 16231, 16181, 16131]
ground_loc = locs[int(probes.split('-')[0]) - 1]
terminal_loc = locs[int(probes.split('-')[1]) - 1]


#current
i0 = 1
#position of probes 
left = 4121
right = 16181
log_status("Reading file...",0)
input_file = 'outputs/etching_final_surface.txt' 
data = np.loadtxt(input_file,skiprows=1)
if data[1, 1] == 0.0:
    txtformat = 1
else:
    txtformat = 0
   
if txtformat == 1:    
    r1=data[:, 0]
    x1=data[:, 1]
    y1=data[:, 2]
    z1=data[:, 3]

    mask = (data[:, 1] != skip_value2) & (data[:, 2] != skip_value1)
    filtered_data = data[mask]
    r2 = filtered_data[:, 0]
    X2 = filtered_data[:, 1]
    Y2 = filtered_data[:, 2]
    Z2 = filtered_data[:, 3]

    mask = (X2 != skip_value2) & (Y2 != skip_value1)
    rf = r2[mask].reshape(inputdomain, inputdomain)
    Xf = X2[mask].reshape(inputdomain, inputdomain)
    Yf = Y2[mask].reshape(inputdomain, inputdomain)
    Zf = Z2[mask].reshape(inputdomain, inputdomain)
    
    n_missing = np.isnan(Zf).sum()
    if n_missing > 0:
        print(f"Warning: {n_missing} missing points — filling by interpolation")
        mask = np.isnan(Zf)
        if mask.any():
            # For each NaN pixel, find the index of the nearest non-NaN pixel
            _, nearest_idx = distance_transform_edt(mask, return_indices=True)
            Zf[mask] = Zf[nearest_idx[0][mask], nearest_idx[1][mask]]

    new_size = inputdomain // block_size  
    #new_size = 1000 // block_size 
    rc = rf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    Xc = Xf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    Yc = Yf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    Zc = Zf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    rc1 = rc.flatten()
    Xc1 = Xc.flatten()
    Yc1 = Yc.flatten()
    Zc1 = Zc.flatten()

else :
    sorted_data = data[np.lexsort((data[:, 2], data[:, 1]))]
    r1=sorted_data[:, 0]
    x1=sorted_data[:, 1]
    y1=sorted_data[:, 2]
    z1=sorted_data[:, 3]

    mask = (sorted_data[:, 1] != skip_value2) & (sorted_data[:, 2] != skip_value1)
    filtered_data = sorted_data[mask]
    r2 = filtered_data[:, 0]
    X2 = filtered_data[:, 1]
    Y2 = filtered_data[:, 2]
    Z2 = filtered_data[:, 3]

    mask = (X2 != skip_value2) & (Y2 != skip_value1)
    rf = r2[mask].reshape(inputdomain, inputdomain)
    Xf = X2[mask].reshape(inputdomain, inputdomain)
    Yf = Y2[mask].reshape(inputdomain, inputdomain)
    Zf = Z2[mask].reshape(inputdomain, inputdomain)

    n_missing = np.isnan(Zf).sum()
    if n_missing > 0:
        print(f"Warning: {n_missing} missing points — filling by interpolation")
        mask = np.isnan(Zf)
        if mask.any():
            # For each NaN pixel, find the index of the nearest non-NaN pixel
            _, nearest_idx = distance_transform_edt(mask, return_indices=True)
            Zf[mask] = Zf[nearest_idx[0][mask], nearest_idx[1][mask]]

    new_size = inputdomain // block_size  
    #new_size = 1000 // block_size 
    rc = rf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    Xc = Xf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    Yc = Yf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    Zc = Zf.reshape(new_size, block_size, new_size, block_size).mean(axis=(1, 3))
    rc1 = rc.flatten()
    Xc1 = Xc.flatten()
    Yc1 = Yc.flatten()
    Zc1 = Zc.flatten() 

# Dictionary to store unique materials
materials_dict = {}
domain_material_map = {}
# Parameters
n_domains = int(inputdomain/block_size)
domain_size = 0.01 / n_domains  # 1cm = 0.01m divided into parts
total_domains = n_domains * n_domains
if not os.path.exists(f'models/comsol_model_domains{n_domains}.mph'):
    
    # Create a new model
    model = client.create('performance')
    
    # Create geometry
    model.java.component().create('comp1', True)
    model.java.component('comp1').geom().create('geom1', 2)

    # Create all square domains
    geom = model.java.component('comp1').geom('geom1')

    log_status(f"Total domains {total_domains} ...",10)
    log_status(f"Creating domains ...",10)
    domain_count = 0

    for i in range(n_domains):
        for j in range(n_domains):
            tag = f"r_{i}_{j}"
            x_start = i * domain_size
            y_start = j * domain_size
            
            # Create rectangle for each domain
            rect = geom.create(tag, 'Rectangle')
            rect.set('pos', [x_start, y_start])
            rect.set('size', [domain_size, domain_size])
            
            domain_count += 1
        
        if (i + 1) % 5 == 0:
            log_status(f"  Created {(i + 1) * n_domains} domains...",10+30*float(i/n_domains))


    model.save(f'models/comsol_model_domains{n_domains}.mph')
else:
    model = client.load(f'models/comsol_model_domains{n_domains}.mph')
    geom = model.java.component('comp1').geom('geom1')
    
    geom_string = geom.toString()
    domain_count = len(str(geom_string.split('Child nodes:')[1]).split(',')) - 1

# Build geometry
log_status("Building geometry...",45)
geom.run()
log_status(f"Geometry built successfully! Total domains: {domain_count}",50)

log_status("Determining material properties...",50)

#Relative permittivity, n-type Bi2Te2.7Se0.3, 98, J. Appl. Phys. 126, 164503 (2019) https://doi.org/10.1063/1.5117183
#electrical conductivity of Bi2Te3 RT, ~1e5 S/m https://heegermaterials.com/semiconductor-materials/626-bismuth-telluride-bi2te3-cas-1304-82-1.html
#electrical conductivity of Bi2Se3 RT, 1.9e5 S/m https://link.springer.com/article/10.1007/s10948-017-3999-3 1-3e5 S/m
#Relative permittivity, Al, 1
#electrical conductivity, Al, 3.77e7 S/m https://www.engineeringtoolbox.com/conductors-d_1381.html

# Vectorized material property assignment
n_total = n_domains * n_domains
conductivity_arr = np.empty(n_total)
permittivity_arr = np.empty(n_total)

cond1 = rc1 > 0
cond2 = (rc1 == 0) & (Zc1 > 100)
cond3 = (rc1 == 0) & (Zc1 < 6)
cond_else = ~(cond1 | cond2 | cond3)

conductivity_arr[cond1] = 3.8e7
permittivity_arr[cond1] = 1
conductivity_arr[cond2] = 1.5e5
permittivity_arr[cond2] = 100
conductivity_arr[cond3] = 1e7
permittivity_arr[cond3] = 100
conductivity_arr[cond_else] = 3e5 * np.cos((np.pi / 5) * np.round(Zc1[cond_else], 1)) + 3.1e5
permittivity_arr[cond_else] = 100

# Build materials dict and domain map
for i in range(n_total):
    mat_id = f"{conductivity_arr[i]:.6e}_{permittivity_arr[i]:.1f}"
    if mat_id not in materials_dict:
        materials_dict[mat_id] = {
            'conductivity': conductivity_arr[i],
            'permittivity': permittivity_arr[i]
        }
    domain_material_map[i + 1] = mat_id

# Weighted average (excluding high-conductivity domains)
weight_mask = conductivity_arr <= 9e6
weighted_conductivity_sum = np.sum(conductivity_arr[weight_mask])
total_weight = np.sum(weight_mask)

if total_weight > 0:
    weighted_avg_conductivity = weighted_conductivity_sum / total_weight
else:
    weighted_avg_conductivity = 0  

#print(f"Created 10000 domains with {len(materials_dict)} unique materials")

# Create materials
log_status("Creating materials...",60)
mat_component = model.java.component('comp1').material()

mat_counter = 1
mat_name_map = {}  # Map material ID to material name in COMSOL

for mat_id, properties in materials_dict.items():
    mat_name = f'mat{mat_counter}'
    mat_name_map[mat_id] = mat_name
    
    mat_component.create(mat_name)
    mat_obj = mat_component.get(mat_name)
    mat_obj.label(f'Material_{mat_counter}')
    
    # Set electrical conductivity and relative permittivity
    prop_group = mat_obj.propertyGroup('def')
    prop_group.set('electricconductivity', str(properties['conductivity']))
    prop_group.set('relpermittivity', str(properties['permittivity']))
    
    mat_counter += 1

#print(f"Created {len(materials_dict)} unique materials")

# Assign materials to domains
log_status("Assigning materials to domains...",70)

# Group domains by material for efficient assignment
material_domains = {}
for domain_idx, mat_id in domain_material_map.items():
    mat_name = mat_name_map[mat_id]
    if mat_name not in material_domains:
        material_domains[mat_name] = []
    material_domains[mat_name].append(domain_idx)

# Assign materials
for mat_name, domain_list in material_domains.items():
    mat_component.get(mat_name).selection().set(domain_list)

log_status("Material assignment complete",80)

model.java.component('comp1').physics().create('ec', 'ConductiveMedia', 'geom1')
ec = model.java.component('comp1').physics('ec')
terminal = ec.create('term1', 'Terminal', 1)
terminal.set('TerminalType', 'Current')
terminal.set('I0', str(i0))  # 1 A current
terminal.selection().set([terminal_loc])

#lower outside(51,20126)
#lower inside(4071,16131)
#middle outside(101,20176)
#middle inside(4121,16181)
#upper outside(151,20226)
#upper inside(4171,16231)    
## Create ground boundary condition
ground = ec.create('gnd1', 'Ground', 1)
ground.selection().set([ground_loc])

## Create mesh
model.java.component('comp1').mesh().create('mesh1')
model.java.component('comp1').mesh('mesh1').autoMeshSize(4)  # Fine mesh
model.java.component('comp1').mesh('mesh1').run()

# Create study
model.java.study().create('std1')
model.java.study('std1').create('stat', 'Stationary')

# Run the study
log_status("Running simulation...",90)
model.java.study('std1').run()
#model.solve('std1')

log_status("Extracting results...",95)
    
try:
    # Evaluate voltage on all domains (use 'volume' for 2D, which means area)
    print("Extracting voltage data...")
    voltage = model.evaluate('V', 'volume')
    x_coords = model.evaluate('x', 'volume')
    y_coords = model.evaluate('y', 'volume')
    
    # Calculate voltage difference
    max_voltage = np.max(voltage)
    min_voltage = np.min(voltage)
    voltage_diff = max_voltage - min_voltage
    
#    print(f"\nResults:")
#    print(f"Max Voltage: {max_voltage:.6e} V")
#    print(f"Min Voltage: {min_voltage:.6e} V")
#    print(f"Voltage difference: {voltage_diff:.6e} V")
    
    # Calculate conductance G = I/V
    current = 1.0  # A
    conductance = current / voltage_diff if voltage_diff != 0 else np.inf
  
    # Print conductance prominently
    print("\n" + "=" * 60)
    print(f"Conductance: {conductance:.6e} S")
    print(f"Effective Conductivity: {weighted_avg_conductivity:.6e} S/m")    
    print("=" * 60 + "\n")
    

except Exception as e:
    print(f"Error during result extraction: {e}")
    print("\nTrying alternative evaluation on boundaries...")
    
    # Try evaluating on boundaries
    try:
        voltage_bound = model.evaluate('V', 'boundary')
        x_bound = model.evaluate('x', 'boundary')
        y_bound = model.evaluate('y', 'boundary')
        
        max_voltage = np.max(voltage_bound)
        min_voltage = np.min(voltage_bound)
        voltage_diff = max_voltage - min_voltage
        current = 1.0
        conductance = current / voltage_diff if voltage_diff != 0 else np.inf
        
        
    except Exception as e2:
        print(f"Alternative method also failed: {e2}")
        print("Please check the COMSOL GUI for results.")

log_status("Saving model...",97)
model.save('models/comsol_model.mph')
model.clear()

np.save('outputs/conductance.npy', conductance)
np.save('outputs/effective_conductivity.npy', weighted_avg_conductivity)

log_status("Simulation completed!",100)
