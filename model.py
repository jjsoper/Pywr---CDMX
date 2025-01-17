# -------------------
# PYWR MODEL - CDMX
# -------------------

from pywr.core import Model, Input, Output, Link, Storage
from pywr.parameters.parameters import ArrayIndexedParameter
from pywr.recorders.recorders import NumpyArrayNodeRecorder, CSVRecorder, NumpyArrayStorageRecorder
from datetime import datetime
from warnings import warn
import json
import ast
import pandas as pd
import xlrd

# ----------------- PROJECT LEVEL ROUTINES -----------------------

# Load data
with open('Corrected Shape.json') as network_data:
    data = json.load(network_data)
    nodes_list = data["network"]["nodes"]
    links_list = data["network"]["links"]
    template = data['template']

# TODO: link option and scenario to call routine?
# Define option and scenarios
option = "Baseline"
scenario = "Simulation - No Restrictions"

# Define OA link types used in model
link_types = ['Virtual Link', 'Virtual Link - Bidirectional', 'Virtual Groundwater Link', 'Conveyance']

# Define OA node types used in model
storage_types = ['Storage Tank']
input_types = ['Misc Source', 'Surface Water', 'Groundwater']
output_types = ['Outflow Node', 'Urban Demand', 'General Demand']
misc_types = ['Lifting Station', 'Pumping Plant', 'Treatment Plant', 'Diversion Reservoir', 'Junction']

# cutzamala supply data (correction for bug in OA)
sheet = xlrd.open_workbook('Cutzamala Supply.xlsx')
sheet = sheet.sheet_by_index(0)
cutzamala_supply = sheet.col_values(0)

# Define save folder to store results (used for CSVRecorders)
results_folder = 'C:/Users/Josh Soper/Documents/Mexico/Summer 2018/Analysis'

# ----------------- CREATE MODEL -----------------------

# TODO: End time needs to be last day of final model year, not first day of next year
for scenarios in data['network']['scenarios']:
    if scenarios['name'] == option:
        meta = scenarios
        start = datetime.strptime(meta['start_time'], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
        # end = datetime.strptime(meta['end_time'], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d')
        end = '2015-12-31'
        if meta['time_step'] == 'day':
            ts = 1

model = Model(start=start, end=end, timestep=ts, solver='glpk')

# -----------------GENERATE NETWORK STRUCTURE -----------------------

# create node dictionaries by name and id

node_lookup_name = {}
node_lookup_id = {}

for node in nodes_list:
    types = [t for t in node['types'] if abs(t['template_id']) == abs(template['id'])]
    node_lookup_name[node.get('name')] = {
        'type': types[0]['name'] if types else None,
        'id': node['id']
    }
    node_lookup_id[node.get("id")] = {
        'type': types[0]['name'] if types else None,
        'name': node.get("name"),
        'attributes': node['attributes']
    }

# create link dictionaries by name and id
# create pywr links dictionary with format ["name" = pywr type (Link) + 'name']
# define number of in and out connections for each node in node_lookup_id

link_lookup = {}
link_lookup_id = {}
pywr_links = {}

for link in links_list:
    name = link['name']
    link_id = link['id']
    node_1_id = link['node_1_id']
    node_2_id = link['node_2_id']
    node_lookup_id[node_2_id]['connect_in'] = node_lookup_id[node_2_id].get('connect_in', 0) + 1
    node_lookup_id[node_1_id]['connect_out'] = node_lookup_id[node_1_id].get('connect_out', 0) + 1
    link_lookup[name] = {
        'id': link_id,
        'node_1_id': node_1_id,
        'node_2_id': node_2_id,
        'from_slot': node_lookup_id[node_1_id]['connect_out'] - 1,
        'to_slot': node_lookup_id[node_2_id]['connect_in'] - 1
    }
    link_lookup_id[link_id] = {
        'name': link['name'],
        'type': link['types'][0]['name'],
        'node_1_id': node_1_id,
        'node_2_id': node_2_id,
        'from_slot': node_lookup_id[node_1_id]['connect_out'] - 1,
        'to_slot': node_lookup_id[node_2_id]['connect_in'] - 1,
        'attributes': link['attributes']
    }
    pywr_links[link_id] = Link(model, name=name)

#  remove unconnected (rogue) nodes from analysis
connected_nodes = []
for link_id, trait in link_lookup_id.items():
    connected_nodes.append(trait['node_1_id'])
    connected_nodes.append(trait['node_2_id'])
rogue_nodes = []
for node in node_lookup_id:
    if node not in connected_nodes:
        rogue_nodes.append(node)
for node in rogue_nodes:
    del node_lookup_id[node]


# create pywr nodes dictionary with format ["name" = pywr type + 'name']
# separate non_storage dicts for greater flexibility with recording model results
storage = {}
non_storage = {}
non_storage_outputs = {}
non_storage_junctions = {}
non_storage_inputs = {}
non_storage_types = input_types + output_types + misc_types

for node_id, node_trait in node_lookup_id.items():
    types = node_trait['type']
    name = node_trait['name']
    if types in storage_types:
        num_outputs = node_trait['connect_in']
        num_inputs = node_trait['connect_out']
        storage[node_id] = Storage(model, name=name, num_outputs=num_outputs, num_inputs=num_inputs)
    elif types in output_types:
        non_storage_outputs[node_id] = Output(model, name=name)
    elif types in misc_types:
        non_storage_junctions[node_id] = Link(model, name=name)
    elif types in input_types:
        non_storage_inputs[node_id] = Input(model, name=name)
    else:
        raise Exception("Oops, missed a type!")

non_storage = {**non_storage_inputs, **non_storage_outputs, **non_storage_junctions}

# Generate dict of pywr network components
pywr_components = {"inputs": non_storage_inputs, "outputs": non_storage_outputs, "junctions": non_storage_junctions,
                    "storage": storage, "links": pywr_links}

# create network connections
# must assign connection slots for storage, from_slot removes water from system, to_slot adds water to system

for link_id, link_trait in link_lookup_id.items():
    up_node = link_trait['node_1_id']
    down_node = link_trait['node_2_id']
    if node_lookup_id[up_node]['type'] not in storage_types and \
            node_lookup_id[down_node]['type'] not in storage_types:
                non_storage[up_node].connect(pywr_links[link_id])
                pywr_links[link_id].connect(non_storage[down_node])
    elif node_lookup_id[up_node]['type'] in storage_types and \
            node_lookup_id[down_node]['type'] not in storage_types:
                storage[up_node].connect(pywr_links[link_id], from_slot=link_trait['from_slot'])
                pywr_links[link_id].connect(non_storage[down_node])
    elif node_lookup_id[up_node]['type'] not in storage_types and \
            node_lookup_id[down_node]['type'] in storage_types:
                non_storage[up_node].connect(pywr_links[link_id])
                pywr_links[link_id].connect(storage[down_node], to_slot=link_trait['to_slot'])
    else:
         storage[up_node].connect(pywr_links[link_id], from_slot=link_trait['from_slot'])
         pywr_links[link_id].connect(storage[down_node], to_slot=link_trait['to_slot'])


# -------------------- INPUT DATA --------------------

# match resource attribute id's to resource attribute name by node type / for use in populate_data function
def find(node_type, attr_id):
    for types in data['template']['types']:
        if node_type == types['name']:
            for attributes in types['typeattrs']:
                if attr_id == attributes['attr_id']:
                    return attributes['attr_name']

# select scenarios for model run
def select_scenario(option, scenario):
    scenario_data = {}
    for scenarios in data['network']['scenarios']:
        if scenarios['name'] == option:
            scenario_data['option'] = scenarios['resourcescenarios']
        elif scenarios['name'] == scenario:
            scenario_data['scenario'] = scenarios['resourcescenarios']
    return scenario_data

# Specify and isolate scenarios used in input data: "Baseline" and "Simulation - No Restrictions"
scenario_data = select_scenario(option, scenario)

# fill in relevant data based on resource lookup dictionary and resource id
# operates under assumption that all timeseries data comes in as cms and need to convert to Mcm/day
resource_errors = []
def populate_data(lookup, resource):
    for att in lookup[resource]['attributes']:
        if scenario_data.get('scenario') != None:
            for att_id in scenario_data['scenario']:
                if att['id'] == att_id['resource_attr_id']:
                    att['data_type'] = find(lookup[resource]['type'], att['attr_id'])
                    if ast.literal_eval(att_id['value']['metadata'])['use_function'] == 'Y':
                        att['data'] = str(ast.literal_eval(att_id['value']['metadata'])['function'])
                    else:
                        try:
                            data_list = list(ast.literal_eval(att_id['value']['value'])['0'].values())
                            data_list = [i * 0.0864 for i in data_list]
                            att['data'] = data_list
                            keys_list = list(ast.literal_eval(att_id['value']['value'])['0'].keys())
                            if start not in keys_list[0] and end not in keys_list[len(keys_list) - 1]:
                                resource_errors.append(lookup[resource]['name'])
                        except:
                            pass
        if scenario_data.get('option') != None:
            for att_id in scenario_data['option']:
                if att['id'] == att_id['resource_attr_id'] and 'data_type' not in att:
                    att['data_type'] = find(lookup[resource]['type'], att['attr_id'])
                    if ast.literal_eval(att_id['value']['metadata'])['use_function'] == 'Y':
                        att['data'] = ast.literal_eval(att_id['value']['metadata'])['function']
                    else:
                        try:
                            data_list = list(ast.literal_eval(att_id['value']['value'])['0'].values())
                            data_list = [i * 0.0864 for i in data_list]
                            att['data'] = data_list
                            keys_list = list(ast.literal_eval(att_id['value']['value'])['0'].keys())
                            if start not in keys_list[0] and end not in keys_list[len(keys_list) - 1]:
                                resource_errors.append(lookup[resource]['name'])
                        except:
                            pass

# populate node_lookup_id with data types and values from specified scenarios
for node in node_lookup_id:
    populate_data(node_lookup_id, node)
for link in link_lookup_id:
    populate_data(link_lookup_id, link)
# raise warning if input data timeseries doesn't match model timestepper
if resource_errors:
    warn("Error in data inputs: {0} incorrect timeseries sequence".format(resource_errors))

# TODO: remove unpopulated resource attributes from node_lookup_id

# populate data in pywr node and link types; functions as constants and time series as lists
# 0.0864 converts cms to Mcm/day
# TODO: don't like matching pywr type to str, need to change

for node in non_storage:
    if 'pywr.nodes.Output' in str(non_storage[node].__class__):
        for att in node_lookup_id[node]['attributes']:
            if att.get('data_type') == 'Priority':
                    non_storage[node].cost = -1*(100-float(att['data']))
            elif att.get('data_type') == 'Demand':
                try:
                    max_flow = ArrayIndexedParameter(model, att['data'])
                    non_storage[node].max_flow = max_flow
                except KeyError:
                    non_storage[node].max_flow = 0

for node in non_storage:
    if 'pywr.nodes.Input' in str(non_storage[node].__class__):
        for att in node_lookup_id[node]['attributes']:
            if att.get('data_type') == 'Supply':
                max_flow = ArrayIndexedParameter(model, att['data'])
                min_flow = ArrayIndexedParameter(model, att['data'])
                non_storage[node].max_flow = max_flow
                non_storage[node].min_flow = min_flow
            elif att.get('data_type') == 'Natural Recharge':
                max_flow = ArrayIndexedParameter(model, att['data'])
                min_flow = ArrayIndexedParameter(model, att['data'])
                non_storage[node].max_flow = max_flow
                non_storage[node].min_flow = min_flow

# temporary fix to OpenAgua data
non_storage[-27326].max_flow = ArrayIndexedParameter(model, cutzamala_supply)
non_storage[-27326].min_flow = ArrayIndexedParameter(model, cutzamala_supply)

for node in non_storage:
    if 'pywr.nodes.Link' in str(non_storage[node].__class__):
        for att in node_lookup_id[node]['attributes']:
            if att.get('data_type') == 'Flow Capacity':
                    non_storage[node].max_flow = float(att['data'])

for node in storage:
    for att in node_lookup_id[node]['attributes']:
        if att.get('data_type') == 'Priority':
                storage[node].cost = -1*(100-float(att['data']))
        elif att.get('data_type') == 'Initial Storage':
                storage[node].initial_volume = float(att['data'])
        elif att.get('data_type') == 'Storage Capacity':
                storage[node].max_volume = float(att['data'])

for link in pywr_links:
    for att in link_lookup_id[link]['attributes']:
        if att.get('data_type') == 'Flow Capacity':
                pywr_links[link].max_flow = float(att['data'])

# adjust default max flow for following node types to 0
for node in node_lookup_id:
    if node_lookup_id[node]['type'] in ['Urban Demand', 'General Demand',
                                        'Misc Source', 'Surface Water', 'Groundwater']:
        if non_storage[node].max_flow == float('inf'):
            non_storage[node].max_flow = 0

# -------------------- ASSIGN RECORDERS --------------------
# CSV recorders: nodes= must be an iterable, name= used to create multiple csv recorders
# Storage CSV displays change in storage at each timestep, to record storage volume per timestep,
#       use NumpyArrayStorageRecorder or add post-processing routine for csv model outputs

for node_set, nodes in pywr_components.items():
    CSVRecorder(model, csvfile='{}/{}.csv'.format(results_folder, node_set),
                                nodes=list(nodes.values()), name="{0}".format(node_set))

# Numpy array recorder (to dataframe)

# def assign_recorders(lookup, resource_class, recorder, pywr_type):
#     for resource_id in lookup:
#         if lookup[resource_id]['type'] in resource_class:
#             lookup[resource_id]['recorder'] = recorder(model, pywr_type[resource_id])
#
# assign_recorders(node_lookup_id, non_storage_types, NumpyArrayNodeRecorder, non_storage)
# assign_recorders(link_lookup_id, link_types, NumpyArrayNodeRecorder, pywr_links)
# assign_recorders(node_lookup_id, storage_types, NumpyArrayStorageRecorder, storage)

# --------------------- RUN MODEL -----------------------------
model.run()
# -------------------- ORGANIZE RESULTS --------------------

# Numpy array to dataframe results
# def get_results(lookup, resource_class):
#     dataframes = []
#     for resource_id in lookup:
#         if lookup[resource_id]['type'] in resource_class:
#             dataframe = lookup[resource_id]['recorder'].to_dataframe()
#             dataframe.columns = [lookup[resource_id]['name']]
#             dataframes.append(dataframe)
#     return pd.concat(dataframes, axis=1)
#
# delivery_results = get_results(node_lookup_id, 'Urban Demand')
# storage_results = get_results(node_lookup_id, storage_types)
# outflow_results = get_results(node_lookup_id, 'Outflow Node')
# supply_results = get_results(node_lookup_id, input_types)
# link_results = get_results(link_lookup_id, link_types)
#
# storage_volumes = {}
# for node in storage:
#     storage_volumes[storage[node].name] = storage[node].max_volume
#
# observed = []
# for node, attributes in node_lookup_id.items():
#     for attribute in attributes['attributes']:
#         if attribute.get('data_type') == 'Observed Delivery':
#             obsdel = pd.DataFrame(attribute['data'])
#             obsdel.columns = [node_lookup_id[node]['name']]
#             observed.append(obsdel)
# observed = pd.concat(observed, axis=1)
