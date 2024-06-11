# -*- coding: utf-8 -*-
"""
Defines samples points on the input space for the expensive simulation to be run on.

Options:
- --sample-only: only draw samples and write sample to their folder
- --prepare-one <dir>: only create files for the directory *dir* containing a sample

If no option is given, does the sampling and prepares all the simulation directories.

Usage:
    python [--sample-only] [--prepare-one <dir>]

@author: François Straet
"""

import json, os, sys, shutil, pathlib

import numpy as np
import pandas as pd

from pyDOE import lhs

sys.path.append(".." + os.sep + ".." + os.sep + "Dispa-SET")

import dispaset as ds

from config import *
from reference import ReferenceInfo, build_reference

try:
    sample_only = (sys.argv[1] == "--sample-only")
    prepare_one = (sys.argv[1] == "--prepare-one" and sys.argv[2])
except IndexError:
    sample_only = False
    prepare_one = False

# from CLOUX's work
capacity_ratio_range = (0.3, 1.3)
share_flex_range     = (0.25, 0.90)
share_storate_range  = (0, 3.0)
share_wind_range     = (0, 0.55)
share_pv_range       = (0, 0.35) 
rntc_range           = (0, 0.75)    

ranges = [capacity_ratio_range, share_flex_range,
          share_storate_range,  share_wind_range,
          share_pv_range,       rntc_range]

ranges_name = ["Capacity ratio", "Share flexible",
               "Share storage",  "Share wind",
               "Share PV",       "rNTC"]

N_DIMS = len(ranges)

def main():
    if prepare_one:
        try:
            n = int(sys.argv[2])
        except ValueError:
            raise ValueError(f"Expected index (in [0-{N_SAMPLES-1}])")
        if n < 0 or n >= N_SAMPLES:
            raise ValueError(f"Index {n} out of range [0-{N_SAMPLES-1}]")

        samples = pd.read_csv(SIMULATIONS_DIR + os.sep + SAMPLES_CSV_NAME, index_col=0)
        # row = pd.read_csv(path + os.sep + SAMPLE_CSV_NAME, index_col=0).squeeze("columns")
        sample = samples.loc[n,:]

        cur_folder = SIMULATIONS_DIR + os.sep + format_folder_name(n, sample)
        return prepare_simulation_files(sample, cur_folder)

    print(f"Writing {'samples' if sample_only else 'simulations'} in {SIMULATIONS_DIR}")

    print(f"Generating samples in ranges {ranges}")
    # samples is numpy array
    samples = lhs(N_DIMS, samples=N_SAMPLES, criterion=CRITERION)

    # scale sample on [0,1] interval to actual ranges
    for i, interval in enumerate(ranges):
        min = interval[0]
        max = interval[1]
        samples[:,i] *= max - min
        samples[:,i] += min
    
    if WRITE_POINTS_TO_CSV or sample_only:
        df = pd.DataFrame(samples, columns=ranges_name)

        out_name = SIMULATIONS_DIR + os.sep + SAMPLES_CSV_NAME
        print(f"Output samples to file: {out_name}")

        df.to_csv(out_name, index_label="Index")
    
    build_simulations(samples, sample_only)
    print(f"Simulations successfully written in {SIMULATIONS_DIR}")

def format_folder_name(index, sample):
    """
    Produces a nicely formatted folder name
    
    :index:         the index of the sample
    :sample:        a python list containing all the values
    """
    return f"sim-{index}_" + "-".join([f"{x:.2f}" for x in sample])
    # return f"sim-{i}_" + np.array2string(sample, separator="-", formatter={'float_kind': lambda x: f"{x:.2f}" })[1:-1]


def build_simulations(samples, sample_only=False):
    """
    Builds the simulations, or only writes samples, based on the samples array

    :samples:       np.ndarray, all the samples
    :sample_only:   bool, if true, only write the samples files.
    """
    nb = len(samples)
    for i, sample in enumerate(samples):
        print(f"Simulation {i} / {nb}, {sample}")
        
        cur_folder = SIMULATIONS_DIR + os.sep + format_folder_name(i, sample.tolist())
        
        if not sample_only:
            prepare_simulation_files(sample, cur_folder)

def prepare_simulation_files(sample, cur_folder):
    """
    Creates the files needed for the simulation to be run

    :sample:        the sample to create the simulation files for
    :cur_folder:    the location to write the simulation files in
    """
    print(f"Preparing files in {cur_folder}")

    if not os.path.exists(REFERENCE_INFO_FILE):
        build_reference(REFERENCE_INFO_FILE)
    
    # make super sure the folder doesn't exist to ensure it'll actually write stuff
    if os.path.exists(cur_folder):
        shutil.rmtree(cur_folder)

    refinfo = ReferenceInfo.deserialize(REFERENCE_INFO_FILE)
    peak_load, flex_units, slow_units, CF_wton, CF_wtof, CF_pv, ref_values = refinfo.tolist()
    capacity_ratio, share_flex, share_sto, share_wind, share_pv, rNTC = sample

    FC_load = 0.736 #based on reference database demand
    
    #if share_flex > 0.905:
    #    print("Killing stalling simulation at the root")
    #    return 1

    
    # ADJUST STORAGE:
    data = ds.adjust_capacity(REFERENCE_SIMULATION_DIR, ('BATS','OTH'), singleunit=True, 
                                value=peak_load*share_sto)

    # ADJUST CAPACITY_RATIO : Variable dépendante de share storage et de share flex =>
    resultat = []
    base_units = flex_units.append(slow_units)
    for index in base_units:
         terme = index.split('_')[1]  # Récupérer le deuxième terme
         terme_suivant = index.split('_')[2]  # Récupérer le troisième terme
         tuple_actuel = (terme, terme_suivant)
         if tuple_actuel not in resultat:  # Vérifier si le tuple n'est pas déjà dans la liste
             resultat.append(tuple_actuel)
             data = ds.adjust_capacity(data, tuple_actuel, scaling=(capacity_ratio)/(ref_values['overcapacity']), singleunit=True)
 
    
    
    # ADJUST FLEX
    data = ds.adjust_flexibility(data, flex_units, slow_units, share_flex, singleunit=True)
    
    
    # dispa-set function to adjust the ntc:
    # ADJUST NTC
    data = ds.adjust_ntc(data, value=rNTC/ref_values['rNTC'])
    
   
    tmp = cur_folder + os.sep + "Inputstmp.gdx"
    # tmp = os.environ["LOCALSCRATCH"] + os.sep + "Inputs.gdx"
    
    # For wind and PV, the units should be lumped into a single unit:
        # ADJUST WIND AND PV :
    #
    perc_off = 0.3 # 2050 capacity installed : 100 000MWe * CF_wtof = 37 800
    perc_on = 0.7 # 2050 capacity installed : 400 000MWe* CF_wton =  100 000
    
    data = ds.adjust_capacity(data, ('WTOF','WIN'),
                            value=(peak_load*FC_load)/CF_wtof*(perc_off*share_wind), singleunit=True)
    data = ds.adjust_capacity(data, ('WTON','WIN'),
                            value=(peak_load*FC_load)/CF_wton*(perc_on*share_wind), singleunit=True)
    data = ds.adjust_capacity(data, ('PHOT','SUN'),
                            value=(peak_load*FC_load)*share_pv/CF_pv, singleunit=True,write_gdx=True,dest_path=cur_folder, temp_path=tmp)
    
    


if __name__ == "__main__":
    main()
