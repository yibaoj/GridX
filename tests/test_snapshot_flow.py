"""Snapshot flow joins, reference directions, interface geometry and saved limits."""
from types import SimpleNamespace

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.text import Annotation
import pandas as pd
import pypsa
import pytest
from shapely.geometry import Point, LineString

from src.app.uc.io import operation_dataset
from src.app.uc.snapshot import prepare_snapshot_flow, plot_snapshot
from src.visualization.flow import interface_geometry, draw_interfaces


def fixture_result():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2024-01-01', periods=2, freq='h'))
    n.add('Bus', ['a','b','d','external'])
    n.add('Line', 'ab', bus0='a', bus1='b', x=.1, s_nom=100, s_max_pu=.8)
    n.add('Link', 'converter', bus0='b', bus1='d', p_nom=40, efficiency=.98)
    n.add('Link', 'interface', bus0='external', bus1='a', p_nom=20)
    n.lines_t.p0 = pd.DataFrame([20.,-40.],index=n.snapshots,columns=['ab'])
    n.lines_t.p1 = -n.lines_t.p0
    n.links_t.p0 = pd.DataFrame([[10.,5.],[0.,10.]],index=n.snapshots,columns=n.links.index)
    n.links_t.p1 = -n.links_t.p0.mul(n.links.efficiency)
    buses = gpd.GeoDataFrame(dict(uid=['a','b','d'],voltage_kv=[500.,500.,800.],
        subclass=['junction_bus','station_bus','station_bus']),
        geometry=[Point(0,0),Point(2,0),Point(2,1)],crs=4326)
    branch = gpd.GeoDataFrame(dict(uid=['ab'],current_type=['AC'],voltage_kv=[500.],
        **{'class':['branch']},from_bus_uid=['a'],to_bus_uid=['b']),
        geometry=[LineString([(2,0),(1,.2),(0,0)])],crs=4326)
    converter = gpd.GeoDataFrame(dict(uid=['converter'],**{'class':['converter']},
        from_bus_uid=['b'],to_bus_uid=['d']),geometry=[Point(2,0)],crs=4326)
    interfaces = gpd.GeoDataFrame(dict(uid=['interface'],**{'class':['interface']},
        internal_bus_uid=['a'],external_bus_uid=['external'],voltage_kv=[500.]),
        geometry=[LineString([(-2,0),(-1,.2),(0,0)])],crs=4326)
    case = SimpleNamespace(network=SimpleNamespace(bus=SimpleNamespace(data=buses),
        branch=SimpleNamespace(data=branch),transformer=SimpleNamespace(data=converter.iloc[:0]),
        converter=SimpleNamespace(data=converter)), interface=SimpleNamespace(data=interfaces),
        spatial=gpd.GeoDataFrame(dict(admin_uid=[],spatial_level=[]),geometry=[],crs=4326))
    return SimpleNamespace(network=n,case=case,data=operation_dataset(n,n.snapshots,timezone='UTC'))


def test_saved_snapshot_limits_and_electrical_direction():
    r = fixture_result()
    r.network = None  # Saved results must work without reconstructing a backend.
    buses, flows = prepare_snapshot_flow(r,'2024-01-01 01:00')
    flows = flows.set_index('uid')
    assert flows.at['ab','p0_mw'] == -40
    assert flows.at['ab','loading_pu'] == pytest.approx(.5)
    assert flows.at['interface','from_bus_uid'] == 'external'
    assert flows.at['interface','to_bus_uid'] == 'a'
    assert flows.at['interface','loading_pu'] == pytest.approx(.5)
    assert flows.at['converter','p0_mw'] == 0
    assert set(flows.kind) == {'ac','converter','interface'}
    assert buses.loc[buses.uid.eq('external')].geometry.iloc[0].equals(Point(-2,0))
    with pytest.raises(ValueError,match='exact result timestamp'):
        prepare_snapshot_flow(r,'2024-01-01 00:30')
    original = r.case.network.bus.data.copy()
    _, high = prepare_snapshot_flow(r,'2024-01-01 00:00',minimum_voltage_kv=750)
    assert high.empty
    pd.testing.assert_frame_equal(original,r.case.network.bus.data)


def test_reversed_geometry_arrows_and_bilingual_maps():
    r = fixture_result()
    for language in ['en','zh']:
        fig = plot_snapshot(r,'2024-01-01 01:00',language=language,map_crs='EPSG:4326',china_inset=False)
        arrows = [obj for obj in fig.axes[0].texts if isinstance(obj,Annotation)]
        assert len(arrows) == 2  # Zero-flow converter has no arrow.
        # Negative ab flow travels from b (right) towards a (left), despite reversed geometry.
        assert arrows[0].xy[0] < arrows[0].get_position()[0]
        # Positive interface flow travels from external (left) towards internal (right).
        assert arrows[1].xy[0] > arrows[1].get_position()[0]
        fig.canvas.draw()
        plt.close(fig)


def test_static_interface_overlay_and_voltage_filter():
    r = fixture_result()
    buses, interfaces = r.case.network.bus.data, r.case.interface.data
    original = interfaces.copy()
    lines, virtual = interface_geometry(buses, interfaces)
    assert lines.to_bus_uid.tolist() == ['external']
    assert virtual.uid.tolist() == ['external']
    fig, axis = plt.subplots()
    draw_interfaces(fig,buses,interfaces,map_crs='EPSG:4326',language='en')
    assert {t.get_text() for t in axis.get_legend().texts} == {'Interface','Virtual external bus'}
    plt.close(fig)
    lines, virtual = interface_geometry(buses, interfaces,750)
    assert lines.empty and virtual.empty
    pd.testing.assert_frame_equal(original,interfaces)


def test_export_interface_uses_electrical_endpoints_not_geometry_order():
    r = fixture_result()
    # Same geographic route, but an export has internal bus0 and external bus1.
    r.data['link_bus0'].loc[dict(link_uid='interface')] = 'a'
    r.data['link_bus1'].loc[dict(link_uid='interface')] = 'external'
    _, flows = prepare_snapshot_flow(r,'2024-01-01 01:00')
    row = flows.set_index('uid').loc['interface']
    assert row.from_bus_uid == 'a' and row.to_bus_uid == 'external'
    fig = plot_snapshot(r,'2024-01-01 01:00',language='en',map_crs='EPSG:4326',china_inset=False)
    arrows = [obj for obj in fig.axes[0].texts if isinstance(obj,Annotation)]
    assert arrows[-1].xy[0] < arrows[-1].get_position()[0]
    plt.close(fig)


def test_animated_directions_follow_signed_flows_and_skip_zero_flows():
    r = fixture_result()
    animation = plot_snapshot(r,'2024-01-01 01:00',language='en',
                             map_crs='EPSG:4326',china_inset=False,animate=True)
    particles = animation._func(0)[0]
    start = np.asarray(particles.get_offsets()).copy()
    animation._func(1)
    end = np.asarray(particles.get_offsets()).copy()
    assert len(start) == 4  # Two markers per active branch; zero converter excluded.
    assert np.all(end[:2,0] < start[:2,0])  # Negative Line flow goes right to left.
    assert np.all(end[2:,0] > start[2:,0])  # Import goes external to internal.
    animation._fig.canvas.draw()
    plt.close(animation._fig)
