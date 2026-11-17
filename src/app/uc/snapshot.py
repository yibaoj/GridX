"""Prepare a single solved snapshot for the shared network-flow renderer."""
from __future__ import annotations

import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

from ...visualization import configure_matplotlib, resolve_plot_settings
from ...visualization.flow import interface_geometry, plot_flow_map
from ...visualization.network import filter_network_voltage
from ...visualization.spatial import spatial_background


def plot_snapshot(result, snapshot, *, minimum_voltage_kv=None, language=None,
                  map_crs=None, china_inset=None, figsize=(13,9), label_flows=False,
                  animate=False):
    """Return a Figure, or FuncAnimation with animate=True, for one solved timestamp."""
    settings = resolve_plot_settings(language=language, map_crs=map_crs, china_inset=china_inset)
    configure_matplotlib(settings)
    buses, flows = prepare_snapshot_flow(result, snapshot, minimum_voltage_kv=minimum_voltage_kv)
    return plot_flow_map(buses, flows, spatial=spatial_background(result.case.spatial),
        snapshot=pd.Timestamp(snapshot), map_crs=settings.map_crs,
        china_inset=settings.china_inset, language=settings.language,
        figsize=figsize, label_flows=label_flows, animate=animate)


def prepare_snapshot_flow(result, snapshot, *, minimum_voltage_kv=None):
    """Join solved flows, solved limits, electrical endpoints, and case geometries."""
    snapshot = pd.Timestamp(snapshot)
    if snapshot not in pd.DatetimeIndex(result.data.time.values):
        raise ValueError(f'No solved snapshot at {snapshot}; select an exact result timestamp.')
    case = result.case
    buses, branches = filter_network_voltage(case.network.bus.data, case.network.branch.data,
                                             minimum_voltage_kv=minimum_voltage_kv)
    interfaces = None if case.interface is None else case.interface.data
    if interfaces is not None and len(interfaces):
        interfaces, virtual = interface_geometry(buses, interfaces, minimum_voltage_kv)
        buses = gpd.GeoDataFrame(pd.concat([buses,virtual],ignore_index=True),crs=buses.crs)
    points = buses.set_index('uid').geometry.representative_point()
    network = result.network
    if network is None and any(f'{component}_p_limit_upper_mw' not in result.data
                               for component in ('line','transformer','link')):
        warnings.warn('Saved results lack branch limits; using current case backend ratings. '
                      'Re-run UC to persist the exact solved limits.', RuntimeWarning, stacklevel=2)
        network = case.to_pypsa(strict=False)
    tables = []
    for component, frame in [('line',branches.loc[branches.current_type.eq('AC')]),
            ('link',branches.loc[branches.current_type.eq('DC')]),
            ('transformer',case.network.transformer.data),('link',case.network.converter.data),
            ('link',interfaces)]:
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        dimension = f'{component}_uid'
        frame = frame.loc[frame.uid.isin(result.data[dimension].values)]
        if frame.empty:
            continue
        for port in (0,1):
            frame[f'p{port}_mw'] = result.data[f'{component}_p{port}_mw'].sel(time=snapshot,
                **{dimension:frame.uid.to_numpy()}).values
            frame['from_bus_uid' if port == 0 else 'to_bus_uid'] = result.data[f'{component}_bus{port}'].sel(
                **{dimension:frame.uid.to_numpy()}).values
        frame = frame.loc[frame.from_bus_uid.isin(points.index)&frame.to_bus_uid.isin(points.index)].copy()
        if frame.empty:
            continue
        if component == 'transformer' or (component == 'link' and frame['class'].eq('converter').all()):
            frame['geometry'] = [LineString([points[a],points[b]]) for a,b in zip(frame.from_bus_uid,frame.to_bus_uid)]
        frame['kind'] = ('ac' if component == 'line' else 'transformer' if component == 'transformer'
                         else 'interface' if frame['class'].eq('interface').all()
                         else 'converter' if frame['class'].eq('converter').all() else 'dc')
        upper, lower = _limits(result, component, snapshot, frame.uid, network)
        frame['limit_mw'] = np.where(frame.p0_mw.ge(0),upper,-lower)
        frame['loading_pu'] = frame.p0_mw.abs().div(frame.limit_mw.where(frame.limit_mw.gt(0)))
        tables.append(frame[['uid','from_bus_uid','to_bus_uid','kind','p0_mw','p1_mw','limit_mw','loading_pu','geometry']])
    flows = gpd.GeoDataFrame(pd.concat(tables,ignore_index=True) if tables else
        pd.DataFrame(columns=['uid','from_bus_uid','to_bus_uid','kind','p0_mw','p1_mw','limit_mw','loading_pu','geometry']),
        geometry='geometry',crs=buses.crs)
    return buses, flows


def _limits(result, component, snapshot, uids, network):
    dimension = f'{component}_uid'
    if f'{component}_p_limit_upper_mw' in result.data:
        return tuple(result.data[f'{component}_p_limit_{side}_mw'].sel(time=snapshot,
            **{dimension:uids.to_numpy()}).values for side in ('upper','lower'))
    name = {'line':'Line','transformer':'Transformer','link':'Link'}[component]
    table = network.components[name].static.reindex(uids)
    if component == 'link':
        capacity = table.p_nom.where(~table.p_nom_extendable,table.p_nom_opt).to_numpy()
        return tuple(network.get_switchable_as_dense(name,f'p_{side}_pu',[snapshot]).loc[snapshot,uids].to_numpy()*capacity
                     for side in ('max','min'))
    capacity = table.s_nom.where(~table.s_nom_extendable,table.s_nom_opt).to_numpy()
    upper = network.get_switchable_as_dense(name,'s_max_pu',[snapshot]).loc[snapshot,uids].to_numpy()*capacity
    return upper,-upper
