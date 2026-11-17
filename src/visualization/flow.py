"""Shared network geometry and rendering for boundary interfaces and snapshot flows."""
from __future__ import annotations

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, to_hex
from matplotlib.lines import Line2D
from matplotlib.transforms import ScaledTranslation
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import linemerge

INTERFACE_COLOR = '#9b32b2'
FLOW_LINE_STYLES = {'dc': '--', 'ac': '-', 'interface': '-.'}
IDLE_COLOR = '#b8bfbd'
PARTICLE_COLOR = '#9ca5a8'
EQUIPMENT_MARKERS = {'converter': ('^', 4), 'transformer': ('P', -4)}
BUS_STYLES = (
    ('junction_bus', 'o', 12),
    ('station_bus', 's', 18),
    ('external_bus', 'D', 14),
)


def interface_geometry(buses, interfaces, minimum_voltage_kv=None):
    """Keep source routes; place virtual nodes at the external route endpoints."""
    lines = interfaces.copy()
    if minimum_voltage_kv is not None:
        lines = lines.loc[lines.voltage_kv.ge(minimum_voltage_kv)]
    lines = lines.loc[lines.internal_bus_uid.isin(buses.uid)].copy()
    points = buses.set_index('uid').geometry.representative_point()
    external = []
    for row in lines.itertuples():
        route = _route(row.geometry)
        endpoints = [Point(route.coords[0]), Point(route.coords[-1])]
        external.append(max(endpoints, key=points[row.internal_bus_uid].distance))
    virtual = gpd.GeoDataFrame(dict(uid=lines.external_bus_uid.to_numpy(),
        voltage_kv=lines.voltage_kv.to_numpy(), subclass='external_bus'),
        geometry=external, crs=buses.crs)
    lines['from_bus_uid'] = lines.internal_bus_uid
    lines['to_bus_uid'] = lines.external_bus_uid
    return lines, virtual


def draw_interfaces(figure, buses, interfaces, *, map_crs, language, minimum_voltage_kv=None):
    """Overlay static interface routes and virtual nodes on an existing network map."""
    if interfaces is None or interfaces.empty:
        return
    lines, virtual = interface_geometry(buses, interfaces, minimum_voltage_kv)
    if lines.empty:
        return
    lines, virtual = lines.to_crs(map_crs), virtual.to_crs(map_crs)
    for axis in figure.axes:
        lines.plot(ax=axis, color=INTERFACE_COLOR, linestyle='-.', linewidth=.5, alpha=.75, zorder=8)
        virtual.plot(ax=axis, color=INTERFACE_COLOR, marker='D', markersize=3,
                     edgecolor='white', linewidth=.2, zorder=9)
        _expand_extent(axis, lines)
    axis = figure.axes[0]
    axis.set_title(axis.get_title() + f' | {len(lines)} ' + ('边界接口' if language == 'zh' else 'interfaces'))
    handles = list(axis.get_legend().legend_handles) if axis.get_legend() else []
    handles.extend(_interface_handles(language))
    axis.legend(handles=handles, loc='lower left', ncol=2, frameon=False, fontsize=8)


def plot_flow_map(buses, flows, *, spatial, snapshot, map_crs, china_inset,
                  language, figsize, label_flows=False, animate=False):
    """Use uniform line widths, loading colors and optional moving direction markers."""
    from ..standard.plot import map_axes, draw_background, draw_boundaries, finish_map
    figure, axes = map_axes(spatial, china_inset=china_inset, figsize=figsize)
    buses, flows = buses.to_crs(map_crs).copy(), flows.to_crs(map_crs).copy()
    buses['geometry'] = buses.geometry.representative_point()
    points = buses.set_index('uid').geometry
    cmap = plt.colormaps['YlOrRd'].with_extremes(bad='#46788e')
    norm = Normalize(0, 1, clip=True)
    active_flows = flows.loc[flows.p0_mw.abs().gt(1e-3)]
    # Buses have no branch-style rating. Use the highest active incident loading.
    bus_loading = pd.concat([
        active_flows.set_index(endpoint).loading_pu
        for endpoint in ('from_bus_uid', 'to_bus_uid')
    ]).groupby(level=0).max()
    bus_colors = bus_loading.map(lambda value: to_hex(cmap(norm(value)))).to_dict()
    buses['_flow_color'] = buses.uid.map(lambda uid: bus_colors.get(uid, IDLE_COLOR))
    motion = []
    for index, axis in enumerate(axes):
        routes = []
        draw_background(axis, spatial, map_crs=map_crs)
        draw_boundaries(axis, spatial, map_crs=map_crs, zorder=1)
        for row in flows.itertuples():
            route = _route(row.geometry)
            # OSM geometry order need not match electrical bus0/bus1 order.
            if Point(route.coords[-1]).distance(points[row.from_bus_uid]) < Point(route.coords[0]).distance(points[row.from_bus_uid]):
                route = LineString(list(route.coords)[::-1])
            active = abs(row.p0_mw) > 1e-3
            color = cmap(norm(row.loading_pu)) if active else IDLE_COLOR
            width = .85
            x, y = route.xy
            axis.plot(x, y, color=color, linewidth=width,
                      linestyle=FLOW_LINE_STYLES.get(row.kind, ':'), zorder=3)
            if row.kind in EQUIPMENT_MARKERS:
                marker, offset = EQUIPMENT_MARKERS[row.kind]
                midpoint = route.interpolate(.5, normalized=True)
                # Separate colocated equipment and bus symbols in display coordinates.
                transform = axis.transData
                if route.length == 0:
                    transform += ScaledTranslation(offset / 72, 5 / 72, figure.dpi_scale_trans)
                axis.plot(midpoint.x, midpoint.y, marker=marker, linestyle='none',
                          color=color, markersize=5.5, markeredgecolor='#40494d',
                          markeredgewidth=.65, transform=transform, zorder=12)
            if active and route.length:
                if animate:
                    routes.append((route, 1 if row.p0_mw > 0 else -1))
                else:
                    start, end = (.43, .57) if row.p0_mw > 0 else (.57, .43)
                    a, b = route.interpolate(start, normalized=True), route.interpolate(end, normalized=True)
                    axis.annotate('', xy=(b.x,b.y), xytext=(a.x,a.y),
                        arrowprops=dict(arrowstyle='-|>', color=color, lw=width, mutation_scale=9), zorder=5)
                if label_flows:
                    p = route.interpolate(.6, normalized=True)
                    axis.text(p.x,p.y,f'{abs(row.p0_mw):,.0f}',fontsize=6,zorder=7)
        if animate:
            particles = axis.scatter([], [], s=7, c=PARTICLE_COLOR,
                                     edgecolors='white', linewidths=.25, zorder=7)
            motion.append((particles,routes))
        for order, (subclass, marker, size) in enumerate(BUS_STYLES, start=9):
            layer = buses.loc[buses.subclass.eq(subclass)]
            if len(layer):
                layer.plot(ax=axis, marker=marker, color=layer['_flow_color'].tolist(),
                           markersize=size, edgecolor='#596267', linewidth=.45, zorder=order)
        _expand_extent(axis, flows)
        title = ('电网潮流' if language == 'zh' else 'Network power flow') + f' | {snapshot}'
        finish_map(axis,title if index == 0 else '')
    zh = language == 'zh'
    handles = [Line2D([], [], color=IDLE_COLOR, linestyle=style, label=label)
               for style, label in zip(FLOW_LINE_STYLES.values(),
                   ('直流线路', '交流线路', '省间联络线（Interface）') if zh else
                   ('DC line', 'AC line', 'Interregional line (Interface)'), strict=True)]
    handles += [Line2D([], [], marker=marker, color=IDLE_COLOR, linestyle='none',
                       markeredgecolor='#40494d', markersize=5.5, label=label)
                for (marker, _), label in zip(EQUIPMENT_MARKERS.values(),
                    ('换流器', '变压器') if zh else ('Converter', 'Transformer'), strict=True)]
    handles += [Line2D([], [], marker=marker, color=IDLE_COLOR, linestyle='none',
                       markeredgecolor='#596267',
                       markersize=np.sqrt(size), label=label)
                for (_, marker, size), label in zip(BUS_STYLES,
                    ('连接母线', '站点母线', '虚拟外部母线') if zh else
                    ('Junction bus', 'Station bus', 'Virtual external bus'), strict=True)]
    if animate:
        handles.append(Line2D([],[],marker='.',color=PARTICLE_COLOR,linestyle='none',
                              label='移动点表示流向（速度无物理含义）' if zh else 'Moving dots: direction only'))
    if active_flows.loading_pu.isna().any():
        handles.append(Line2D([], [], color=cmap(np.nan),
                              label='有潮流，容量未知' if zh else 'Flow with unknown limit'))
    axes[0].legend(handles=handles,loc='lower left',ncol=2,fontsize=7,framealpha=.85).set_zorder(20)
    bar = figure.colorbar(ScalarMappable(norm=norm,cmap=cmap), ax=axes, shrink=.65, pad=.025)
    bar.set_label('有功负载率 |P| / 容量（非视在功率负载率）' if zh else 'Active-power loading |P| / limit')
    bar.set_ticks([0,.25,.5,.75,1],labels=['0%','25%','50%','75%','100%'])
    if animate:
        from matplotlib.animation import FuncAnimation

        def update(frame):
            for particles, routes in motion:
                positions = [route.interpolate((phase + direction*frame/32) % 1, normalized=True)
                             for route,direction in routes for phase in (.15,.65)]
                particles.set_offsets(np.array([(p.x,p.y) for p in positions]).reshape(-1,2))
            return [particles for particles,_ in motion]

        return FuncAnimation(figure,update,frames=32,interval=80,blit=False)
    return figure


def _interface_handles(language):
    return [Line2D([],[],color=INTERFACE_COLOR,linestyle='-.',linewidth=.5,
                   label='边界接口' if language == 'zh' else 'Interface'),
            Line2D([],[],color=INTERFACE_COLOR,marker='D',markersize=3,linestyle='none',
                   label='虚拟外部母线' if language == 'zh' else 'Virtual external bus')]


def _route(geometry):
    if geometry.geom_type == 'MultiLineString':
        geometry = linemerge(geometry)
        if geometry.geom_type == 'MultiLineString':
            raise ValueError('Cannot draw directional flow on a disconnected route.')
    return geometry


def _expand_extent(axis, frame):
    if frame.empty or getattr(axis, '_map_is_inset', False):
        return
    x0,y0,x1,y1 = frame.total_bounds
    left,right = axis.get_xlim(); bottom,top = axis.get_ylim()
    pad = max(x1-x0,y1-y0,1)*.03
    bounds = (min(left,x0-pad),max(right,x1+pad),min(bottom,y0-pad),max(top,y1+pad))
    axis._map_projected_bounds = bounds
    axis.set_xlim(bounds[:2]); axis.set_ylim(bounds[2:])
