"""PyPSA boundary exchange adapter; costs and flows use the selected direction."""


def add_interfaces(network, component):
    if component is None or component.data.empty:
        return
    for carrier in ("boundary_exchange", "external_supply", "external_sink"):
        if carrier not in network.carriers.index:
            network.add("Carrier", carrier)
    for row in component.data.itertuples(index=False):
        values = component.parameter.loc[component.parameter.uid.eq(row.uid)].set_index('name').value
        external = row.external_bus_uid
        internal = row.internal_bus_uid
        network.add("Bus", external, v_nom=network.buses.at[internal, 'v_nom'],
                    carrier=network.buses.at[internal, 'carrier'])
        importing = row.direction == 'import'
        network.add("Generator", f"external-device:{row.uid}", bus=external,
            carrier='external_supply' if importing else 'external_sink',
            sign=1 if importing else -1, p_nom=row.rated_power_mw, marginal_cost=0)
        network.add("Link", row.uid,
            bus0=external if importing else internal, bus1=internal if importing else external,
            carrier='boundary_exchange', p_nom=row.rated_power_mw, efficiency=1,
            p_min_pu=values['power_min_pu'], p_max_pu=values['power_max_pu'],
            marginal_cost=values['marginal_cost_eur_per_mwh'])
        network.links.loc[row.uid, 'interface_direction'] = row.direction
        network.links.loc[row.uid, 'source_uid'] = row.source_uid
        network.links.loc[row.uid, 'energy_min_pu'] = values['energy_min_pu']
        network.links.loc[row.uid, 'energy_max_pu'] = values['energy_max_pu']


def interface_constraints(network, snapshots):
    """Apply energy bounds independently to the current optimization window."""
    names = network.links.index[network.links.carrier.eq('boundary_exchange')]
    if names.empty:
        return
    import xarray as xr
    weights = xr.DataArray(network.snapshot_weightings.generators.loc[snapshots], dims='snapshot')
    dispatch = network.model['Link-p'].sel(name=names)
    energy = (dispatch * weights).sum('snapshot')
    capacity_hours = network.links.loc[names, 'p_nom'] * float(weights.sum())
    for bound, sense in (('min', '>='), ('max', '<=')):
        rhs = xr.DataArray((capacity_hours * network.links.loc[names, f'energy_{bound}_pu']).to_numpy(float),
                           coords={'name': names}, dims='name')
        network.model.add_constraints(energy, sense, rhs, name=f'Interface-energy-{bound}')
