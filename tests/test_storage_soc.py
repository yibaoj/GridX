"""SOC boundary conditions for complete single and rolling simulations."""
import numpy as np
import pandas as pd
import pypsa
import pytest

from src.app.uc.io import operation_dataset
from src.app.uc.manager import _configure_storage_soc, _optimize


@pytest.mark.parametrize('mode', ['single', 'rolling_horizon'])
def test_soc_boundaries_and_separate_charge_discharge(mode):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2024-01-01', periods=4, freq='h'))
    n.snapshot_weightings.loc[:, :] = 2.0
    n.add('Bus', 'bus')
    n.add('Generator', 'generator', bus='bus', p_nom=100,
          marginal_cost=pd.Series([100, 100, 1, 1], index=n.snapshots))
    n.add('Load', 'load', bus='bus', p_set=10)
    n.add('StorageUnit', 'storage', bus='bus', p_nom=10, max_hours=2,
          efficiency_store=.9, efficiency_dispatch=.9, cyclic_state_of_charge=True)
    _configure_storage_soc(n, n.snapshots, {})
    assert n.storage_units.at['storage', 'state_of_charge_initial'] == 10
    assert not n.storage_units.at['storage', 'cyclic_state_of_charge']
    assert n.storage_units_t.state_of_charge_set.iloc[:-1].isna().all().all()
    status, condition, _ = _optimize(n, n.snapshots, solve_mode=mode,
        horizon=2, overlap=0, solver_name='highs', log_to_console=False)
    assert (status, condition) == ('ok', 'optimal')
    soc = n.storage_units_t.state_of_charge.storage
    assert soc.iloc[-1] == pytest.approx(10)
    assert soc.iloc[1] < 10 - 1e-6  # Intermediate windows need not meet final SOC.
    charge = n.storage_units_t.p_store.storage
    discharge = n.storage_units_t.p_dispatch.storage
    previous = soc.shift(1).fillna(10)
    np.testing.assert_allclose(soc, previous + 2 * (.9 * charge - discharge / .9), atol=1e-7)
    data = operation_dataset(n, n.snapshots, timezone='UTC')
    np.testing.assert_allclose(data.storage_p_mw,
        data.storage_p_dispatch_mw - data.storage_p_store_mw, atol=1e-7)
    assert 'StorageUnit-state_of_charge_set' in n.model.constraints


def test_soc_fraction_configuration_and_validation():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2024-01-01', periods=2, freq='h'))
    n.add('Bus', 'bus')
    n.add('StorageUnit', 'storage', bus='bus', p_nom=10, max_hours=4)
    _configure_storage_soc(n, n.snapshots, {'initial_soc_pu': .25, 'final_soc_pu': .75})
    assert n.storage_units.at['storage', 'state_of_charge_initial'] == 10
    assert n.storage_units_t.state_of_charge_set.iloc[-1, 0] == 30
    with pytest.raises(ValueError, match='between 0 and 1'):
        _configure_storage_soc(n, n.snapshots, {'final_soc_pu': 1.1})
