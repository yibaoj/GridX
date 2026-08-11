const state = {
  config: null,
  stage: null,
  layer: null,
  resourceClass: null,
  map: null,
  tileLayer: null,
  boundaryLayer: null,
  dataLayer: null,
  chart: null,
  canvasRenderer: null,
};

const colors = {
  bioenergy: "#9b6aa3", coal: "#505552", gas: "#4f82cf", geothermal: "#df8a4f",
  hydropower: "#3d7f87", nuclear: "#8cc55a", solar: "#e7b62f", wind: "#4b9a63",
  other: "#909691", battery: "#8066c2", pumped_hydro: "#3e86a8",
  pumped_storage: "#3e86a8", compressed_air: "#5f78a8",
  thermal_storage: "#c17454", load_shedding: "#c64655",
  onshore: "#4b9a63", offshore_fixed: "#3d7f87", offshore_floating: "#4d78a8",
  utility_scale_pv: "#e7b62f", run_of_river: "#57a7b0", reservoir: "#3976a5",
};

const labels = {
  bioenergy: "生物质", coal: "煤电", gas: "天然气", geothermal: "地热",
  hydropower: "水电", nuclear: "核电", solar: "光伏", wind: "风电", other: "其他",
  battery: "电化学", battery_storage: "电化学", pumped_hydro: "抽水蓄能", compressed_air: "压缩空气",
  compressed_air_storage: "压缩空气", capacitor_storage: "超级电容",
  pumped_storage: "抽水蓄能",
  thermal_storage: "热储能", load_shedding: "负荷损失", load: "负荷",
  onshore: "陆上风电", offshore_fixed: "固定式海风", offshore_floating: "漂浮式海风",
  offshore_unspecified: "海上风电", utility_scale_pv: "集中式光伏",
  run_of_river: "径流式水电", reservoir: "水库水电",
  mixed_reservoir_run_of_river: "混合式水电", solar_thermal: "光热发电",
  electric_load: "电力负荷",
};

async function request(path) {
  const response = await fetch(path);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || response.statusText);
  return body;
}

async function init() {
  state.config = await request("/api/bootstrap");
  state.stage = state.config.map.default_stage;
  state.layer = state.config.map.default_layer;
  state.resourceClass = state.config.manifest.resource_classes[0]?.id || null;
  document.title = `${state.config.site.title} ${state.config.site.title_zh}`;
  document.querySelector("#visit-count").textContent = formatNumber(state.config.visits);
  renderStages();
  renderLayerMenu();
  renderResourceOptions();
  renderNews();
  initMap();
  wireControls();
  await showStage(state.stage);
  if (window.lucide) window.lucide.createIcons();
}

function initMap() {
  const options = state.config.map;
  state.map = L.map("map", { zoomControl: false, minZoom: options.min_zoom, maxZoom: options.max_zoom })
    .setView(options.center, options.zoom);
  L.control.zoom({ position: "bottomright" }).addTo(state.map);
  state.tileLayer = L.tileLayer(options.tile_url, {
    attribution: options.tile_attribution,
    maxZoom: options.max_zoom,
  }).addTo(state.map);
  state.canvasRenderer = L.canvas({ padding: 0.35 });
}

function renderStages() {
  const list = document.querySelector("#stage-list");
  list.innerHTML = state.config.stages.map(stage => `
    <button class="stage-button" type="button" data-stage="${stage.id}">
      <i data-lucide="${stage.icon}"></i>
      <span class="stage-copy"><strong>${stage.label}</strong><small>${stage.description}</small></span>
      <i class="stage-chevron" data-lucide="chevron-right"></i>
    </button>`).join("");
  list.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => showStage(button.dataset.stage));
  });
}

function renderLayerMenu() {
  const menu = document.querySelector("#layer-menu");
  menu.innerHTML = state.config.layers.map(layer => `
    <button class="layer-option" type="button" data-layer="${layer.id}">
      <i data-lucide="${layer.icon}"></i><span>${layer.label}</span><i class="check" data-lucide="check"></i>
    </button>`).join("");
  menu.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => showLayer(button.dataset.layer));
  });
}

function renderResourceOptions() {
  const menu = document.querySelector("#resource-options");
  menu.innerHTML = state.config.manifest.resource_classes.map(item => `
    <button class="resource-option" type="button" data-resource-class="${item.id}">${item.label}</button>
  `).join("");
  menu.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => {
      state.resourceClass = button.dataset.resourceClass;
      showLayer("resource", true);
    });
  });
}

function renderNews() {
  document.querySelector("#news-feed").innerHTML = state.config.news.map(item => `
    <article class="news-item"><time>${item.date}</time><h3>${item.title}</h3><p>${item.content}</p></article>
  `).join("");
}

async function showStage(stageId) {
  if (stageId === state.stage && state.dataLayer) return;
  state.stage = stageId;
  document.querySelectorAll(".stage-button").forEach(button => {
    button.classList.toggle("active", button.dataset.stage === stageId);
  });
  const application = stageId === "application";
  document.querySelector("#map-view").classList.toggle("active", !application);
  document.querySelector("#application-view").classList.toggle("active", application);
  setLoading(true);
  try {
    if (application) await showApplication();
    else {
      await loadBoundary(stageId);
      await showLayer(state.layer, true);
      setTimeout(() => state.map.invalidateSize(), 0);
    }
  } finally {
    setLoading(false);
  }
}

async function loadBoundary(stageId) {
  if (state.boundaryLayer) state.boundaryLayer.removeFrom(state.map);
  const data = await request(`/api/boundary/${stageId}`);
  state.boundaryLayer = L.geoJSON(data, {
    style: feature => {
      const marine = feature.properties.spatial_level === "marine_zone";
      return {
        color: marine ? "#aebfc2" : "#929a94",
        weight: marine ? 0.45 : 0.75,
        opacity: marine ? 0.5 : 0.8,
        fillColor: marine ? "#eaf0ef" : "#f7f8f6",
        fillOpacity: marine ? 0.08 : 0.03,
        interactive: false,
      };
    },
  });
  if (document.querySelector("#boundary-toggle").checked) state.boundaryLayer.addTo(state.map);
}

async function showLayer(layerId, force = false) {
  if (state.stage === "application") return;
  if (!force && layerId === state.layer && state.dataLayer) return;
  state.layer = layerId;
  const metadata = state.config.layers.find(item => item.id === layerId);
  updateLayerControl(metadata);
  setLoading(true);
  try {
    const suffix = layerId === "resource" ? `/${state.resourceClass}` : "";
    const payload = await request(`/api/layers/${state.stage}/${layerId}${suffix}`);
    if (state.dataLayer) state.dataLayer.removeFrom(state.map);
    state.dataLayer = drawLayer(layerId, payload);
    state.dataLayer.addTo(state.map);
    if (state.boundaryLayer) state.boundaryLayer.bringToFront();
    updateStatus(metadata, payload.summary);
  } catch (error) {
    document.querySelector("#record-count").textContent = error.message;
  } finally {
    setLoading(false);
  }
}

function updateLayerControl(metadata) {
  const current = document.querySelector("#layer-current");
  current.innerHTML = `<i data-lucide="${metadata.icon}"></i><span>${metadata.label}</span><i data-lucide="chevron-down"></i>`;
  document.querySelectorAll(".layer-option").forEach(button => button.classList.toggle("active", button.dataset.layer === metadata.id));
  const resourceOptions = document.querySelector("#resource-options");
  resourceOptions.hidden = metadata.id !== "resource";
  resourceOptions.querySelectorAll("button").forEach(button => {
    button.classList.toggle("active", button.dataset.resourceClass === state.resourceClass);
  });
  if (window.lucide) window.lucide.createIcons();
}

function drawLayer(layerId, payload) {
  if (layerId === "generator" || layerId === "storage") return drawAssets(payload.geojson, payload.summary);
  if (layerId === "network") return drawNetwork(payload.geojson, payload.summary);
  return drawContinuous(payload.geojson, payload.summary, layerId);
}

function drawAssets(geojson, summary) {
  const reference = Math.max(Number(summary.capacity_reference_mw), 1);
  const categoryMap = Object.fromEntries(summary.categories.map(item => [item.id, item]));
  setCategoryLegend(summary.categories, summary.capacity_legend_mw, reference);
  return L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => {
      const total = Number(feature.properties.total_mw || 0);
      const size = Math.max(7, 7 + 25 * Math.sqrt(Math.min(total / reference, 1)));
      const entries = Object.entries(feature.properties.breakdown || {}).filter(([, value]) => value > 0);
      if (summary.display_mode === "point") {
        const className = entries[0]?.[0] || "other";
        return L.circleMarker(latlng, {
          renderer: state.canvasRenderer,
          radius: Math.max(2, size * .24),
          color: "#fbfcfa",
          weight: .45,
          fillColor: categoryMap[className]?.color || colors.other,
          fillOpacity: .8,
        });
      }
      let angle = 0;
      const slices = entries.map(([name, value]) => {
        const start = angle;
        angle += 360 * value / total;
        return `${categoryMap[name]?.color || colors.other} ${start}deg ${angle}deg`;
      });
      return L.marker(latlng, { icon: L.divIcon({
        className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
        html: `<div class="pie-marker" style="width:${size}px;height:${size}px;background:conic-gradient(${slices.join(",")})"></div>`,
      })});
    },
    onEachFeature: (feature, layer) => {
      const rows = Object.entries(feature.properties.breakdown || {}).sort((a,b) => b[1]-a[1])
        .map(([name, value]) => [classLabel(name), `${formatNumber(value)} MW`]);
      layer.bindPopup(popup([["位置", feature.properties.location_uid], ["总容量", `${formatNumber(feature.properties.total_mw)} MW`], ...rows]));
    },
  });
}

function drawContinuous(geojson, summary, layerId) {
  const values = geojson.features.map(feature => Number(feature.properties.value)).filter(Number.isFinite);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 1);
  const title = summary.title || (layerId === "population" ? "人口" : layerId === "load" ? "负荷" : classLabel(summary.selected_class));
  setContinuousLegend(title, min, max, layerId, summary.unit);
  return L.geoJSON(geojson, {
    style: feature => {
      const value = Number(feature.properties.value);
      return { color: "#f7f8f6", weight: .25, fillColor: continuousColor((value - min) / Math.max(max - min, 1e-9), layerId), fillOpacity: .8 };
    },
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 3.2, color: "#f7f8f6", weight: .35,
      fillColor: continuousColor((Number(feature.properties.value) - min) / Math.max(max - min, 1e-9), layerId), fillOpacity: .86,
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(popup([
      ["位置", feature.properties.location_uid || feature.properties.spatial_uid || feature.properties.uid],
      [title, `${formatNumber(feature.properties.raw_value ?? feature.properties.value)}${layerId === "population" ? " 人" : ` ${summary.unit || ""}`}`],
    ])),
  });
}

function drawNetwork(geojson, summary) {
  document.querySelector("#legend").hidden = false;
  const styles = Object.fromEntries(summary.branch_legend.map(item => [item.label, item]));
  document.querySelector("#legend").innerHTML = `<div class="legend-title">电压等级与电流类型</div><div class="legend-grid">${summary.branch_legend.map(item => `<div class="legend-item"><span class="legend-line" style="border-top-color:${item.color};border-top-style:${item.dash ? "dashed" : "solid"}"></span>${item.label}</div>`).join("")}</div><div class="legend-title legend-node-title">节点</div><div class="legend-grid">${summary.node_legend.map(item => `<div class="legend-item"><span class="legend-swatch" style="background:${item.color}"></span>${item.label}</div>`).join("")}</div>`;
  return L.geoJSON(geojson, {
    renderer: state.canvasRenderer,
    style: feature => {
      const item = styles[feature.properties.style] || { color: "#7d8581", dash: false };
      return { color: item.color, weight: .8, opacity: .68, dashArray: item.dash ? "5 4" : null };
    },
    pointToLayer: (feature, latlng) => {
      const station = feature.properties.node_type === "station";
      return L.circleMarker(latlng, { renderer: state.canvasRenderer, radius: station ? 2.2 : 1.1, color: station ? "#d1495b" : "#596267", weight: .25, fillColor: station ? "#d1495b" : "#596267", fillOpacity: .72 });
    },
    onEachFeature: (feature, layer) => layer.bindPopup(popup([
      [feature.properties.feature_kind === "bus" ? "母线" : "支路", feature.properties.uid],
      ["电压", `${formatNumber(feature.properties.voltage_kv)} kV`],
      ["类型", String(feature.properties.current_type || "--").toUpperCase()],
    ])),
  });
}

async function showApplication() {
  const payload = await request("/api/application/uc");
  const summary = payload.summary;
  const applicationColors = Object.fromEntries((payload.categories || []).map(item => [item.id, item.color]));
  document.querySelector("#application-summary").innerHTML = [
    ["求解状态", statusLabel(summary.termination_condition)],
    ["模拟时段", `${formatNumber(summary.snapshots)} h`],
    ["网络范围", `${formatNumber(summary.case_minimum_voltage_kv)} kV+`],
    ["负荷损失", `${formatPercent(summary.load_shedding_share)}`],
  ].map(([name, value]) => `<div class="summary-item"><dt>${name}</dt><dd>${value}</dd></div>`).join("");
  if (!state.chart) state.chart = echarts.init(document.querySelector("#dispatch-chart"));
  const chartSeries = payload.series.map(item => {
    const charge = item.kind === "storage_charge";
    const line = item.kind === "load";
    const displayName = line ? "负荷" : `${classLabel(item.id)}${charge ? "充电" : item.kind === "storage_discharge" ? "放电" : ""}`;
    return {
      name: displayName,
      type: "line",
      data: item.values,
      stack: line ? undefined : charge ? "charge" : "supply",
      symbol: "none",
      lineStyle: { width: line ? 1.6 : 0, color: line ? "#171a18" : applicationColors[item.id] || colors[item.id] || colors.other },
      areaStyle: line ? undefined : { opacity: .88, color: applicationColors[item.id] || colors[item.id] || colors.other },
      itemStyle: { color: line ? "#171a18" : applicationColors[item.id] || colors[item.id] || colors.other },
      emphasis: { focus: "series" },
      z: line ? 10 : 2,
    };
  });
  state.chart.setOption({
    animation: false,
    color: chartSeries.map(series => series.itemStyle.color),
    grid: { left: 62, right: 26, top: 34, bottom: 86 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { type: "dashed", color: "#555" } },
      order: "valueDesc",
      formatter: items => `<strong>${formatTime(items[0].axisValue)}</strong>${items.map(item => `<div class="chart-tooltip-row"><span>${item.marker}${item.seriesName}</span><b>${formatNumber(item.value)} MW</b></div>`).join("")}`,
      position: (point, items, element, rectangle, size) => [
        Math.min(point[0] + 12, size.viewSize[0] - size.contentSize[0] - 10),
        Math.max(10, Math.min(point[1] - size.contentSize[1] / 2, size.viewSize[1] - size.contentSize[1] - 10)),
      ],
    },
    legend: { type: "scroll", bottom: 18, left: 24, right: 24, itemWidth: 11, itemHeight: 8, textStyle: { fontSize: 10, color: "#505652" } },
    xAxis: { type: "category", data: payload.time, boundaryGap: false, axisLabel: { formatter: value => formatTime(value), color: "#6f7671" }, axisLine: { lineStyle: { color: "#cbd0cc" } } },
    yAxis: { type: "value", name: "MW", nameTextStyle: { color: "#747b76" }, splitLine: { lineStyle: { color: "#e4e7e4", type: "dashed" } }, axisLabel: { color: "#6f7671" } },
    series: chartSeries,
  }, true);
  setTimeout(() => state.chart.resize(), 0);
}

function setCategoryLegend(categories, capacityValues, reference) {
  const legend = document.querySelector("#legend");
  legend.hidden = false;
  const sizes = capacityValues.map(value => Math.max(7, 7 + 25 * Math.sqrt(Math.min(Number(value) / reference, 1))));
  legend.innerHTML = `<div class="legend-title">类型</div><div class="legend-grid">${categories.map(item => `<div class="legend-item"><span class="legend-swatch" style="background:${item.color}"></span>${item.label}</div>`).join("")}</div><div class="legend-size"><div><div class="legend-title">总容量</div><div class="legend-size-items">${capacityValues.map((value, index) => `<span class="legend-size-item"><i class="legend-circle" style="width:${sizes[index]}px;height:${sizes[index]}px"></i><span>${formatCapacity(value)}</span></span>`).join("")}</div></div></div>`;
}

function setContinuousLegend(title, min, max, layerId, unit) {
  const legend = document.querySelector("#legend");
  legend.hidden = false;
  const gradient = layerId === "load" ? "#f1f3ef,#d2b761,#b35647" : layerId === "population" ? "#f0f3ef,#75a894,#285f50" : "#342a70,#2f87a6,#6cc7a2,#e5c64f";
  legend.innerHTML = `<div class="legend-title">${title}${unit ? `（${unit}）` : ""}</div><div class="legend-scale" style="background:linear-gradient(90deg,${gradient})"></div><div class="legend-range"><span>${formatNumber(min)}</span><span>${formatNumber(max)}</span></div>`;
}

function updateStatus(metadata, summary) {
  const stage = state.config.stages.find(item => item.id === state.stage);
  document.querySelector("#stage-title").textContent = stage.label;
  document.querySelector("#record-count").textContent = `${formatNumber(summary.count)} 条`;
  document.querySelector("#value-total").textContent = summary.value == null ? (summary.unit || metadata.unit) : `${formatNumber(summary.value)} ${summary.unit || metadata.unit}`;
}

function wireControls() {
  document.querySelector("#boundary-toggle").addEventListener("change", event => {
    if (!state.boundaryLayer) return;
    if (event.target.checked) state.boundaryLayer.addTo(state.map);
    else state.boundaryLayer.removeFrom(state.map);
  });
  document.querySelector("#basemap-toggle").addEventListener("change", event => {
    if (event.target.checked) state.tileLayer.addTo(state.map);
    else state.tileLayer.removeFrom(state.map);
  });
  window.addEventListener("resize", () => {
    if (state.chart) state.chart.resize();
  });
}

function popup(rows) {
  return `<div class="popup-title">数据详情</div>${rows.map(([name, value]) => `<div class="popup-row"><span>${name}</span><strong>${value ?? "--"}</strong></div>`).join("")}`;
}

function continuousColor(value, layerId) {
  const stops = layerId === "load" ? [[241,243,239],[210,183,97],[179,86,71]] : layerId === "population" ? [[240,243,239],[117,168,148],[40,95,80]] : [[52,42,112],[47,135,166],[108,199,162],[229,198,79]];
  const scaled = Math.max(0, Math.min(1, value)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const fraction = scaled - index;
  const rgb = stops[index].map((channel, i) => Math.round(channel + (stops[index + 1][i] - channel) * fraction));
  return `rgb(${rgb.join(",")})`;
}

function classLabel(name) { return labels[name] || String(name || "其他").replaceAll("_", " "); }
function setLoading(value) { document.querySelector("#loading").hidden = !value; }
function formatNumber(value) { const number = Number(value); return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN", { maximumFractionDigits: number < 100 ? 2 : 0 }).format(number) : "--"; }
function formatCapacity(value) { const number = Number(value); return number >= 1000 ? `${formatNumber(number / 1000)} GW` : `${formatNumber(number)} MW`; }
function formatPercent(value) { const number = Number(value); return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "--"; }
function statusLabel(value) { return ({ optimal: "最优", feasible: "可行", time_limit: "达到时限" })[value] || value || "--"; }
function formatTime(value) { const date = new Date(value); return `${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")} ${String(date.getHours()).padStart(2,"0")}:00`; }

init().catch(error => {
  setLoading(false);
  document.querySelector("#record-count").textContent = error.message;
});
