const state = {
  config: null,
  map: null,
  tileLayer: null,
  boundaryLayer: null,
  dataLayer: null,
  activeLayer: null,
};

const colors = {
  bioenergy: "#73985b",
  coal: "#4a4e4c",
  gas: "#d6634a",
  geothermal: "#a06b54",
  hydropower: "#4879a8",
  nuclear: "#d8aa38",
  solar: "#d99c31",
  wind: "#4f9b8a",
  battery: "#8367a6",
  pumped_hydro: "#3e87a7",
  compressed_air: "#5c83b5",
  other: "#8f9691",
};

async function request(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error((await response.json()).error || response.statusText);
  return response.json();
}

async function init() {
  state.config = await request("/api/bootstrap");
  document.title = state.config.title;
  document.querySelector("#site-title").textContent = state.config.title;
  document.querySelector("#site-subtitle").textContent = state.config.subtitle;
  renderNavigation();
  renderNews();
  initMap();
  wireControls();
  await Promise.all([loadBoundary(), showLayer(state.config.map.default_layer)]);
  if (window.lucide) window.lucide.createIcons();
}

function initMap() {
  const options = state.config.map;
  state.map = L.map("map", {
    zoomControl: false,
    minZoom: options.min_zoom,
    maxZoom: options.max_zoom,
  }).setView(options.center, options.zoom);
  L.control.zoom({ position: "bottomright" }).addTo(state.map);
  state.tileLayer = L.tileLayer(options.tile_url, {
    attribution: options.tile_attribution,
    maxZoom: options.max_zoom,
  }).addTo(state.map);
}

function renderNavigation() {
  const list = document.querySelector("#layer-list");
  list.innerHTML = state.config.layers.map(layer => `
    <button class="layer-button" type="button" data-layer="${layer.id}" title="${layer.label}">
      <i data-lucide="${layer.icon}"></i>
      <span class="layer-copy"><strong>${layer.label}</strong><p>${layer.description}</p></span>
      <i class="chevron" data-lucide="chevron-right"></i>
    </button>
  `).join("");
  list.querySelectorAll("button").forEach(button => {
    button.addEventListener("click", () => showLayer(button.dataset.layer));
  });
}

function renderNews() {
  document.querySelector("#news-feed").innerHTML = state.config.news.map(item => `
    <article class="news-item">
      <div class="news-meta"><time>${item.date}</time><span>${item.tag}</span></div>
      <h3>${item.title}</h3>
      <p>${item.content}</p>
    </article>
  `).join("");
}

async function loadBoundary() {
  const data = await request("/api/boundary");
  state.boundaryLayer = L.geoJSON(data, {
    style: feature => ({
      color: feature.properties.level === "marine_zone" ? "#83a8ba" : "#8d958f",
      weight: feature.properties.level === "marine_zone" ? 0.8 : 1,
      dashArray: feature.properties.level === "marine_zone" ? "3 3" : null,
      fill: false,
      interactive: false,
    }),
  }).addTo(state.map);
}

async function showLayer(layerId) {
  const metadata = state.config.layers.find(layer => layer.id === layerId);
  if (!metadata || state.activeLayer === layerId) return;
  setLoading(true);
  try {
    const payload = await request(`/api/layers/${layerId}`);
    if (state.dataLayer) state.dataLayer.removeFrom(state.map);
    document.querySelector("#legend").hidden = true;
    state.dataLayer = drawLayer(layerId, payload.geojson);
    state.dataLayer.addTo(state.map);
    if (state.boundaryLayer) state.boundaryLayer.bringToFront();
    state.activeLayer = layerId;
    updateInterface(metadata, payload.summary);
  } catch (error) {
    document.querySelector("#layer-title").textContent = "图层读取失败";
    document.querySelector("#layer-description").textContent = error.message;
  } finally {
    setLoading(false);
  }
}

function drawLayer(layerId, geojson) {
  if (layerId === "spatial") {
    return L.geoJSON(geojson, {
      style: { color: "#547165", weight: 0.55, fillColor: "#b9cbc3", fillOpacity: 0.08 },
      onEachFeature: bindCellPopup,
    });
  }
  if (layerId === "population") return drawPopulation(geojson);
  if (layerId === "generation" || layerId === "storage") return drawAssets(geojson);
  if (layerId === "network") return drawNetwork(geojson);
}

function drawPopulation(geojson) {
  const values = geojson.features.map(feature => Number(feature.properties.population || 0));
  const max = Math.max(...values, 1);
  setContinuousLegend("人口（对数色阶）", 0, max, "#eef3ef", "#2f6d5a");
  return L.geoJSON(geojson, {
    style: feature => ({
      color: "#f8faf8",
      weight: 0.25,
      fillColor: interpolateColor(Math.log1p(feature.properties.population || 0) / Math.log1p(max)),
      fillOpacity: 0.78,
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(popup([
      ["空间单元", feature.properties.spatial_uid],
      ["人口", formatNumber(feature.properties.population)],
    ])),
  });
}

function drawAssets(geojson) {
  const values = geojson.features.map(feature => Number(feature.properties.total_mw || 0));
  const max = Math.max(...values, 1);
  setCategoryLegend(geojson);
  return L.geoJSON(geojson, {
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 3 + 15 * Math.sqrt(feature.properties.total_mw / max),
      color: "#ffffff",
      weight: 0.8,
      fillColor: colors[feature.properties.dominant_class] || colors.other,
      fillOpacity: 0.82,
    }),
    onEachFeature: (feature, layer) => {
      const breakdown = Object.entries(feature.properties.breakdown || {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6)
        .map(([name, value]) => [name, `${formatNumber(value)} MW`]);
      layer.bindPopup(popup([
        ["空间单元", feature.properties.spatial_uid],
        ["总容量", `${formatNumber(feature.properties.total_mw)} MW`],
        ...breakdown,
      ]));
    },
  });
}

function drawNetwork(geojson) {
  document.querySelector("#legend").hidden = true;
  return L.geoJSON(geojson, {
    style: feature => {
      if (feature.properties.feature_kind === "bus") return {};
      const dc = String(feature.properties.current_type || "").toUpperCase() === "DC";
      return { color: dc ? "#d6634a" : "#4879a8", weight: 1.25, opacity: 0.74, dashArray: dc ? "5 4" : null };
    },
    pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
      radius: 2.3,
      color: "#222825",
      weight: 0.6,
      fillColor: "#fbfcfa",
      fillOpacity: 1,
    }),
    onEachFeature: (feature, layer) => layer.bindPopup(popup([
      [feature.properties.feature_kind === "bus" ? "母线" : "支路", feature.properties.uid],
      ["电压", `${formatNumber(feature.properties.voltage_max_kv)} kV`],
      ["类型", feature.properties.current_type || feature.properties.subclass || "--"],
    ])),
  });
}

function bindCellPopup(feature, layer) {
  layer.bindPopup(popup([
    ["空间单元", feature.properties.spatial_uid],
    ["行政单元", feature.properties.admin_uid],
    ["面积", `${formatNumber(feature.properties.area_km2)} km²`],
  ]));
}

function popup(rows) {
  return `<div class="popup-title">数据详情</div>${rows.map(([label, value]) => `
    <div class="popup-row"><span>${label}</span><strong>${value ?? "--"}</strong></div>
  `).join("")}`;
}

function updateInterface(metadata, summary) {
  document.querySelectorAll(".layer-button").forEach(button => {
    button.classList.toggle("active", button.dataset.layer === metadata.id);
  });
  document.querySelector("#layer-title").textContent = metadata.label;
  document.querySelector("#layer-description").textContent = metadata.description;
  document.querySelector("#record-count").textContent = metadata.id === "network"
    ? `${formatNumber(summary.count)} buses · ${formatNumber(summary.secondary_count)} branches`
    : `${formatNumber(summary.count)} records`;
  document.querySelector("#value-total").textContent = summary.value == null
    ? metadata.unit
    : `${formatNumber(summary.value)} ${metadata.unit}`;
  if (window.lucide) window.lucide.createIcons();
}

function setContinuousLegend(title, min, max, low, high) {
  const legend = document.querySelector("#legend");
  legend.hidden = false;
  legend.innerHTML = `
    <div class="legend-title">${title}</div>
    <div class="legend-scale" style="background: linear-gradient(90deg, ${low}, ${high})"></div>
    <div class="legend-range"><span>${formatNumber(min)}</span><span>${formatNumber(max)}</span></div>
  `;
}

function setCategoryLegend(geojson) {
  const classes = [...new Set(geojson.features.map(feature => feature.properties.dominant_class))].sort();
  const legend = document.querySelector("#legend");
  legend.hidden = false;
  legend.innerHTML = `<div class="legend-title">主导类型</div>${classes.map(name => `
    <div class="legend-item"><span class="legend-swatch" style="background:${colors[name] || colors.other}"></span><span>${name}</span></div>
  `).join("")}<div class="legend-size"><span class="size-circle"></span><span>圆面积表示容量</span></div>`;
}

function wireControls() {
  document.querySelector("#boundary-toggle").addEventListener("change", event => {
    if (event.target.checked) state.boundaryLayer.addTo(state.map);
    else state.boundaryLayer.removeFrom(state.map);
  });
  document.querySelector("#basemap-toggle").addEventListener("change", event => {
    if (event.target.checked) state.tileLayer.addTo(state.map);
    else state.tileLayer.removeFrom(state.map);
  });
  document.querySelector("#news-toggle").addEventListener("click", () => {
    document.querySelector("#news-panel").classList.add("open");
  });
  document.querySelector("#news-close").addEventListener("click", () => {
    document.querySelector("#news-panel").classList.remove("open");
  });
}

function setLoading(loading) {
  document.querySelector("#loading").hidden = !loading;
}

function interpolateColor(value) {
  const start = [238, 243, 239];
  const end = [47, 109, 90];
  const rgb = start.map((channel, index) => Math.round(channel + (end[index] - channel) * value));
  return `rgb(${rgb.join(",")})`;
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: number < 100 ? 1 : 0 }).format(number);
}

init().catch(error => {
  setLoading(false);
  document.querySelector("#layer-title").textContent = "网站初始化失败";
  document.querySelector("#layer-description").textContent = error.message;
});
