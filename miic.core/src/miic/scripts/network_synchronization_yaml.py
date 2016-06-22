# -*- coding: utf-8 -*-
"""
Estimate potential errors in the timing of seismic data. The script estimates
time differences between station pairs and performes a minimization to obtain 
consistent estimates of individual clock errors.

The operation is controlled by a parameter file that is passed a command line
argument.
"""
from copy import deepcopy
import datetime
import logging
import os
import sys
import numpy as np
import yaml
import cPickle as pickle

from miic.core.miic_utils import dir_read, mat_to_ndarray, datetime_list, \
    create_path, save_dv
import miic.core.plot_fun as pl
from miic.core.corr_mat_processing import corr_mat_shift, corr_mat_normalize, \
    corr_mat_extract_trace, corr_mat_resample, corr_mat_filter, corr_mat_trim,\
    corr_mat_correct_shift
import miic.core.change_processing as cpr

    
def ini_project(par_file):
    """Initialize a project of network synchrinization
    
    Read the yaml parameter file, and complete its content, i.e. combine
    subdirectory names. Some project relevant directories are created and the 
    parameter dictionary is returned.
    
    :type par_file: str
    :param par_file: path of the yaml parameter file
    :rtype: dict
    :return: parameters
    """
    
    with open(par_file,'rb') as f:
        try:
            par = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(exc)
    create_path(par['proj_dir'])
    par.update({'log_dir':os.path.join(par['proj_dir'],par['log_subdir'])})
    par.update({'fig_dir':os.path.join(par['proj_dir'],par['fig_subdir'])})
    create_path(par['log_dir'])
    create_path(par['fig_dir'])
    
    par['co']['res_dir'] = os.path.join(par['proj_dir'],
                                             par['co']['subdir'])
    
    par['dt']['res_dir'] = os.path.join(par['proj_dir'],
                                             par['dt']['subdir'])
    par['dt']['fig_dir'] = os.path.join(par['fig_dir'],
                                             par['dt']['subdir'])
    par['ce']['res_dir'] = os.path.join(par['proj_dir'],
                                             par['ce']['subdir'])
    
    return par
    

def combination_names(net):
    """Create a list of names with combination names
    
    According to the stations and channel combinations in the parameter a
    list of the espected combination names is created.
    """
    
    combis = []
    for comb in net['comb']:
        n1 = net['stations'][comb['sta'][0]].split('.')[0]
        n2 = net['stations'][comb['sta'][1]].split('.')[0]
        s1 = net['stations'][comb['sta'][0]].split('.')[1]
        s2 = net['stations'][comb['sta'][1]].split('.')[1]
        if comb['cha']=='all':
            for c1 in net['channels']:
                for c2 in net['channels']:            
                    fname = n1+n2+'.'+s1+s2+'.*.'+c1+c2
                    combis.append(fname)
        else:
            assert type(comb['cha']) == type([])
            for cc in comb['cha']:
                fname = n1+n2+'.'+s1+s2+'.*.'+net['channels'][cc[0]]+\
                        net['channels'][cc[1]]
                combis.append(fname)
    return combis


def time_difference_estimation(par):
    """Estimate clock differences between pairs of stations
    
    This function estimates the time differences between the clocks of pairs
    of stations by measuring the shift of noise correlation functions.
    
    :type par: dict
    :param par: project parameters
    """
    
    logging.basicConfig(filename=os.path.join(par['log_dir'],'time_difference_\
        estimation.log'), level=logging.DEBUG, format='%(asctime)s %(message)s')
    logger = logging.getLogger('time_difference_estimation')
    logger.info('Hello')
    create_path(par['dt']['res_dir'])
    create_path(par['dt']['fig_dir'])
    # create lists of times windows for analyzing changes
    start_time_list = datetime_list(par['dt']['start_date'], par['dt']['end_date'],
                                    par['dt']['date_inc'])
    end_time_list = deepcopy(start_time_list)
    wl = datetime.timedelta(seconds=par['dt']['win_len'])
    end_time_list = [x+wl for x in end_time_list]

    # available correlations
    combs = combination_names(par['net'])
    for comb in combs:
        fpattern = 'mat__*'+comb+'*.mat'
        filenames = dir_read(par['co']['res_dir'],fpattern)
        if len(filenames) != 1:
            logging.info('%d files found for correlation matrix matching %s. No processing done.' % (len(filenames),fpattern))
        else:
            filename = filenames[0]
        try:
            logging.info('Working on combination %s' % comb)
            mat = mat_to_ndarray(filename)
            mat = corr_mat_trim(mat,-50,50)
            # normalize the matrix to maxima
            mat = corr_mat_normalize(mat,normtype='absmax')
            # resample the correlation matrix
            mat = corr_mat_resample(mat, start_time_list,end_time_list)
            # filter the matrices
            mat = corr_mat_filter(mat,[par['dt']['freq_min'], par['dt']['freq_max']])
            # make a trimmed copy for plotting
            tmat = corr_mat_trim(mat,-(par['dt']['tw_start']+par['dt']['tw_len']),
                                 (par['dt']['tw_start']+par['dt']['tw_len']))

            # plot correlation matrix
            if par['dt']['plot_corr_matrix']:
                pl.plot_single_corr_matrix(tmat,filename=os.path.join(par['dt']['fig_dir']
                ,tmat['stats']['station']+'_'+tmat['stats']['channel']),clim=[-1,1])
            tw = [np.arange(par['dt']['tw_start']*tmat['stats']['sampling_rate'],(par['dt']['tw_start']+par['dt']['tw_len'])*tmat['stats']['sampling_rate'],1)]
            
            # extract initial reference trace (mean excluding very different traces)
            tr = corr_mat_extract_trace(tmat,method='mean')
            tw = [np.arange(par['dt']['tw_start']*tmat['stats']['sampling_rate'],(par['dt']['tw_start']+par['dt']['tw_len'])*tmat['stats']['sampling_rate'],1)]
            # initial time shift estimation            
            dt = corr_mat_shift(tmat,ref_trc=tr['corr_trace'],return_sim_mat=True,shift_steps=par['dt']['shift_steps'],shift_range=par['dt']['shift_range'],tw=tw)
            # correct the traces for the shift and re-create a better reference
            cmat = corr_mat_correct_shift(mat,dt)
            # make a trimmed copy
            tcmat = corr_mat_trim(cmat,-(par['dt']['tw_start']+par['dt']['tw_len']),
                                 (par['dt']['tw_start']+par['dt']['tw_len']))
            # extract the final reference trace (mean excluding very different traces)
            tr = corr_mat_extract_trace(tcmat,method='mean')
            # obtain an improved time shift measurement
            dt = corr_mat_shift(tmat,ref_trc=tr['corr_trace'],return_sim_mat=True,shift_steps=par['dt']['shift_steps'],shift_range=par['dt']['shift_range'],tw=tw)

            if par['dt']['plot_time_shifts']:
                filename = mat['stats']['station']+'_'+mat['stats']['channel']
                pl.plot_dv(dt,save_dir=par['dt']['fig_dir'],figure_file_name=filename,normalize_simmat=True,sim_mat_Clim=[-1,1.])
            save_dv(dt, '', save_dir=par['dt']['res_dir'])
        except:
            logging.warning('Error processing %s' % filename)
    return 0
    
    
    

def clock_offset_inversion(par):
    """Invert pairwise time differences for individual clock errors
    
    A number of pairwise time difference measurements (shifts of noise
    correlations) are used to estimate the individual clock errors that best 
    explain the time differences.
    
    :type par: dict
    :param par: project parameters
    """
    
    create_path(par['ce']['res_dir'])
    
    DIFFS = {}
    ndata = -1
    for comb in par['net']['comb']:
        station1 = par['net']['stations'][comb['sta'][0]]
        station2 = par['net']['stations'][comb['sta'][1]]
        print station1, station2
        DIFFS.update({station1+'-'+station2:{'diff':[],'comp':[],'corr':[]}})

        for cha in comb['cha']:
            comp = par['net']['channels'][cha[0]]+par['net']['channels'][cha[1]]
            print comp
            file_pattern = '*%s%s.%s%s.*.%s.mat' % (station1.split('.')[0],station2.split('.')[0],station1.split('.')[1],station2.split('.')[1],comp)
            filename = dir_read(par['dt']['res_dir'],file_pattern)[0]
            dt = mat_to_ndarray(filename)
            if ndata < 0:
                ndata = len(dt['time'])
            else:
                if ndata != len(dt['time']):
                    print "Wrong number of measurements"
            DIFFS[station1+'-'+station2]['diff'].append(dt['value'])
            DIFFS[station1+'-'+station2]['comp'].append(comp)
            DIFFS[station1+'-'+station2]['corr'].append(dt['corr'])
        DIFFS[station1+'-'+station2]['diff'] = np.array(DIFFS[station1+'-'+station2]['diff'])
        DIFFS[station1+'-'+station2]['corr'] = np.array(DIFFS[station1+'-'+station2]['corr'])
        DIFFS[station1+'-'+station2].update({'mean_diff':np.mean(DIFFS[station1+'-'+station2]['diff'],axis=0),
                                              'std':np.std(DIFFS[station1+'-'+station2]['diff'],axis=0)})
    # create the matrix to invert
    G = np.zeros([len(par['net']['comb']),len(par['net']['stations'])])
    cnt = 0
    for cnt,comb in enumerate(par['net']['comb']):
        idx1 = comb['sta'][0]
        idx2 = comb['sta'][1]
        station1 = par['net']['stations'][idx1]
        station2 = par['net']['stations'][idx2]
        G[cnt,idx1] = 1
        G[cnt,idx2] = -1
        cnt +=1
    G = G[:,:-1]
    # do the inversion for every measurement

    co = np.zeros((ndata,len(par['net']['stations'])))
    co[:] = np.nan
    d = np.zeros([len(DIFFS),1])
    
    for nd in range(ndata):
        d = np.zeros([len(par['net']['comb']),1])
        for cnt,comb in enumerate(par['net']['comb']):
            idx1 = comb['sta'][0]
            idx2 = comb['sta'][1]
            station1 = par['net']['stations'][idx1]        
            station2 = par['net']['stations'][idx2]
            d[cnt,0] = DIFFS[station1+'-'+station2]['mean_diff'][nd]
        # delete rows in case some measurements are missing
        if nd == 77:
            print d
        tG = deepcopy(G)
        nanind = np.where(np.isnan(d))[0]
        nonanind = np.where(~np.isnan(d))[0]
        tG = np.delete(tG,nanind,axis=0)
        d = np.delete(d,nanind,0)
        # delete columns that only contain zeros (unconstrained stations)
	idy = np.where(np.sum(np.abs(tG),axis=0)==0)[0]
        tG = np.delete(tG,idy,axis=1)
        tm  = np.linalg.lstsq(tG,d)[0]
        # m is the drift of stations[:-1] setting drift of the last station to zero
        tm = np.append(tm,0.)
        cnt = 0
        for idx in  range(len(par['net']['stations'])):
            if idx not in idy:
                co[nd,idx] = tm[cnt]
                cnt += 1

    # adjust for reference stations
    for nd in range(ndata):
        mr = []
        for rs in par['ce']['ref_stations']:
            mr.append(co[nd,par['net']['stations'].index(rs)])
        co[nd,:] -= np.nanmean(np.array(mr))

    return co        
    

            
        
    
    
def clock_drift_inversion(par):
    """Estimate the drifts of individual digitizer clocks
    
    Under the assumption that the error of the digitizer clocks is mostly a
    linear drift, which is a good approximation for ocean bottom seismometers
    the differential drifts of station pairs are inverted for drifts of
    individual clocks.
    
    :type par: dict
    :param par: project parameters
    """

    create_path(par['ce']['res_dir'])

    DRIFTS = {}
    for comb in par['net']['comb']:
        station1 = par['net']['stations'][comb['sta'][0]]
        station2 = par['net']['stations'][comb['sta'][1]]
        print station1, station2
        DRIFTS.update({station1+'-'+station2:{'drift':np.array([]),'comp':np.array([]),'std':np.array([])}})

        for cha in comb['cha']:
            comp = par['net']['channels'][cha[0]]+par['net']['channels'][cha[1]]
            print comp
            file_pattern = '*%s%s.%s%s.*.%s.mat' % (station1.split('.')[0],station2.split('.')[0],station1.split('.')[1],station2.split('.')[1],comp)
            filename = dir_read(par['dt']['res_dir'],file_pattern)[0]

            dt = mat_to_ndarray(filename)
            cdt = cpr.time_select(dt,starttime=par['ce']['drift_start'],endtime=par['ce']['drift_end'])
            w = cpr.estimate_trend(cdt)
            
            DRIFTS[station1+'-'+station2]['drift'] = np.append(DRIFTS[station1+'-'+station2]['drift'],w[0])
            DRIFTS[station1+'-'+station2]['comp'] = np.append(DRIFTS[station1+'-'+station2]['comp'],comp)
            DRIFTS[station1+'-'+station2]['std'] = np.append(DRIFTS[station1+'-'+station2]['std'],w[2])
        
        # combine measurements
        DRIFTS[station1+'-'+station2].update({'mean_drift':np.mean(DRIFTS[station1+'-'+station2]['drift']),'mean_std':np.std(DRIFTS[station1+'-'+station2]['drift'])})

    f = open(os.path.join(par['ce']['res_dir'],'DRIFTS_measurements.pkl'),'w')
    pickle.dump(DRIFTS, f)
    f.close()

    # invert pairwise drift for station drifts
    G = np.zeros([len(DRIFTS),len(par['net']['stations'])])
    d = np.zeros([len(DRIFTS),1])
    cnt = 0
    
    for cnt,comb in enumerate(par['net']['comb']):
        idx1 = comb['sta'][0]
        idx2 = comb['sta'][1]
        station1 = par['net']['stations'][idx1]        
        station2 = par['net']['stations'][idx2]
        d[cnt] = DRIFTS[station1+'-'+station2]['mean_drift']
        G[cnt,idx1] = 1
        G[cnt,idx2] = -1
        cnt +=1
    G = G[:,:-1]
    # do the inversion
    m  = np.linalg.lstsq(G,d)[0]
    # m is the drift of stations[:-1] setting drift of the last station to zero
    m = np.append(m,0.)
    
    # adjust for reference stations
    mr = 0
    for rs in par['ce']['ref_stations']:
        mr += m[par['net']['stations'].index(rs)]
    m -= np.mean(mr)
    
    


    


if __name__=="__main__":
    if len(sys.argv) < 2:
        print 'Specify the parameter file name as first argument.'
        sys.exit()
    par_file = sys.argv[1]
    # initialize the project, create folders and set derived parameters
    par = ini_project(par_file)
    
    # estimate time differences between station pairs    
    time_difference_estimation(par)
    
    # calculate clock erroros assuming either a constant drift or variable
    # clock errors
    if par['ce']['type'] == 'drift':
        clock_drift_inversion(par)
    else:
        co = clock_offset_inversion(par)
    
    
    
