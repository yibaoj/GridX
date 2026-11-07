"""Regional exchange direction, energy budgets, and persistence."""
from dataclasses import replace
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import pypsa
import xarray as xr
from shapely.geometry import Point, LineString, box

from src.case.interface import build_interface
from src.case.backends.interface import add_interfaces
from src.case.io import _write_component, _read_component
from src.case.region import select_region
from src.mapping.model import MappedData
from src.app.uc.manager import _optimize, _validate_dispatch


def interface(direction='import', **options):
    branches = gpd.GeoDataFrame(dict(uid=['crossing'], from_bus_uid=['inside'],
        to_bus_uid=['outside'], voltage_kv=[500.], current_type=['AC']),
        geometry=[LineString([(0,0),(1,0)])], crs=4326)
    return build_interface(branches, {'inside'}, [10.], dict(direction=direction, **options))


@pytest.mark.parametrize('direction', ['import', 'export'])
@pytest.mark.parametrize('mode', ['single', 'rolling_horizon'])
def test_exchange_direction_and_window_energy(direction, mode):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2024-01-01', periods=5, freq='h'))
    n.add('Bus', 'inside')
    n.add('Generator', 'domestic', bus='inside', p_nom=100, marginal_cost=20)
    n.add('Load', 'demand', bus='inside', p_set=pd.Series(20., index=n.snapshots))
    component = interface(direction, energy_min_pu=.4, energy_max_pu=.4,
                          marginal_cost_eur_per_mwh=1)
    add_interfaces(n, component)
    uid = component.data.uid.iloc[0]
    external = component.data.external_bus_uid.iloc[0]
    assert n.links.at[uid, 'bus0'] == (external if direction == 'import' else 'inside')
    assert n.generators.loc[n.generators.carrier.eq('external_sink'), 'sign'].eq(-1).all()
    status, condition, objective = _optimize(n, n.snapshots, solve_mode=mode,
        horizon=2, overlap=0, solver_name='highs', log_to_console=False)
    assert status == 'ok' and condition == 'optimal' and np.isfinite(objective)
    flows = n.links_t.p0[uid]
    windows = [flows] if mode == 'single' else [flows.iloc[i:i+2] for i in range(0,5,2)]
    for window in windows:
        assert window.sum() == pytest.approx(10 * .4 * len(window))
    expected = 100 + (1 if direction == 'export' else -1) * flows.sum()
    assert n.generators_t.p.domestic.sum() == pytest.approx(expected)
    assert _validate_dispatch(n, n.snapshots, tolerance=1e-5)['dispatch_valid']
    from types import SimpleNamespace
    from src.app.uc.io import operation_dataset
    from src.app.uc.plot import prepare_dispatch_data
    result = SimpleNamespace(snapshots=n.snapshots,
        data=operation_dataset(n, n.snapshots, timezone='Asia/Shanghai'),
        case=SimpleNamespace(network=SimpleNamespace(
            bus=SimpleNamespace(data=pd.DataFrame({'uid': ['inside']})))))
    prepared = prepare_dispatch_data(result)
    np.testing.assert_allclose(prepared['generation'].sum(axis=1), prepared['load'])


def test_interface_roundtrip_and_invalid_bounds(tmp_path):
    component = interface('export', power_min_pu=.2, energy_min_pu=.4)
    _write_component(component, tmp_path)
    restored = _read_component(tmp_path)
    pd.testing.assert_frame_equal(component.parameter, restored.parameter)
    assert restored.data.direction.tolist() == ['export']
    with pytest.raises(ValueError, match='do not overlap'):
        interface(power_min_pu=.8, energy_max_pu=.5)
    with pytest.raises(ValueError, match='direction'):
        interface('both')
    with pytest.raises(ValueError, match='bounds'):
        interface(power_max_pu=1.2)


def test_region_filters_all_inputs_without_mutation():
    cells = gpd.GeoDataFrame(dict(spatial_uid=['cell:a','cell:b'], admin_uid=['province:a','province:b']),
        geometry=[box(0,0,1,1),box(1,0,2,1)], crs=4326)
    assets = gpd.GeoDataFrame(dict(uid=['a','b']),geometry=[Point(.5,.5),Point(1.5,.5)],crs=4326)
    series = xr.Dataset({'value': (('time','uid'), [[1,2]])},
        coords={'time': [pd.Timestamp('2024-01-01')], 'uid': ['cell:a','cell:b']})
    population = cells.rename(columns={'admin_uid':'region'}).copy()
    mapped = MappedData(cells,None,assets,assets,None,series,population,series,{})
    result, boundary = select_region(mapped, {'spatial_uids':['province:a']})
    assert result.generator.uid.tolist() == ['a']
    assert result.storage.uid.tolist() == ['a']
    assert result.load.uid.values.tolist() == ['cell:a']
    assert result.resource.uid.values.tolist() == ['cell:a']
    assert result.population.spatial_uid.tolist() == ['cell:a']
    assert len(mapped.generator) == 2
    assert select_region(mapped, {})[0] is mapped
    with pytest.raises(ValueError, match='absent'):
        select_region(mapped, {'spatial_uids':['missing']})


def test_curated_dc_capacity_resolves_by_voltage_and_current_type():
    from src.standard import StandardDataManager
    from src.standard.parameter import _ParameterStandardizer, as_parameter_data
    from src.case.backends.manifest import load_pypsa_manifest
    manager = StandardDataManager('config/standard_data.toml')
    standardizer = _ParameterStandardizer(manager, 'parameter')
    spec = next(s for s in standardizer.options['sources'] if s['source_id'] == 'model_assumptions')
    parameters = as_parameter_data(standardizer._normalize_rows(
        pd.DataFrame(standardizer._long_table_rows(spec))))
    for voltage, capacity in ((500, 3000), (660, 4000), (800, 8000), (1100, 12000)):
        asset = dict(uid=f'dc:{voltage}', **{'class':'branch'}, subclass='dc_overhead_line',
                     current_type='DC', voltage_kv=voltage)
        result = parameters.resolve(asset, 'p_nom_mw_per_circuit', dataset_id='network', at='2025')
        assert result.value.iloc[0] == capacity
        assert result.unit.iloc[0] == 'MW/circuit'
        assert result.group.iloc[0] == 'technical'
        assert result.match_result.iloc[0] == 'eligible'
        asset.update(subclass='ac_overhead_line', current_type='AC')
        assert parameters.resolve(asset, 'p_nom_mw_per_circuit', dataset_id='network').match_rank.iloc[0] == -1
    manifest = load_pypsa_manifest('config/pypsa_parameter_manifest.toml')
    assert manifest.spec('branch_dc_p_nom').name == 'p_nom_mw_per_circuit'
    assert manifest.spec('branch_ac_s_nom').name == 's_nom_mva_per_circuit'


def test_interface_capacity_does_not_load_backend_manifest(monkeypatch):
    from src.case.interface import interface_capacities
    import src.case.backends.manifest as backend
    def forbidden(*args, **kwargs):
        raise AssertionError('Case construction must not read a backend manifest')
    monkeypatch.setattr(backend, 'load_pypsa_manifest', forbidden)
    branches = pd.DataFrame(dict(uid=['ac','dc'], current_type=['AC','DC'], circuits=[2.,1.]))
    parameters = pd.DataFrame(dict(uid=['ac','dc'],
        name=['s_nom_mva_per_circuit','p_nom_mw_per_circuit'],
        unit=['MVA/circuit','MW/circuit'], value=[2350.,8000.]))
    np.testing.assert_allclose(interface_capacities(branches, parameters), [4700.,8000.])
    with pytest.raises(ValueError, match='unresolved'):
        interface_capacities(branches, parameters.iloc[:1])
