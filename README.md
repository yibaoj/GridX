# Paper Codes

本目录保存论文相关的数据处理脚本、notebook、配置和输出。所有Python代码统一在
conda环境 `env-py313` 中编写、测试和运行。

```bash
conda activate env-py313
```

## General Rules

1. **精简变量。** 关键对象使用稳定、语义明确的名称，例如 `osm_file_path`、
   `output_dir`、`grid_topology`、`grid_nodes_gdf` 和 `grid_summary`；仅服务于
   cell、循环或局部处理的中间变量以 `_` 开头。避免为同一对象建立多个别名。
2. **复用单一流程。** 重复功能应由一个函数、一个参数化脚本或一个权威notebook
   完成。OSM拓扑以 `osm_exp.ipynb` 为唯一来源，其他代码直接复用其结果。
3. **保持术语一致。** `lines`、`substations` 来自OSM；`segments` 是线路分段；
   `terminals` 是segment端点；`nodes`、`branches` 只表示最终抽象电网。
4. **分离输入与输出。** 外部原始文件放在 `data/`，代码生成结果放在
   `outputs/`，可复现参数放在 `config/`；不要在分析过程中改写原始数据。
5. **修改后验证。** notebook修改后须在 `env-py313` 中从头执行，检查error
   outputs、统计闭合关系、输出文件和地图可读性。

这些规则同时写入 [AGENTS.md](AGENTS.md)。README本身是项目文档，不保证Codex
每次自动读取；`AGENTS.md` 才是本目录内Codex任务的执行指令。

## 目录

- `osm_exp.ipynb`：OSM高压电网GIS清洗、拓扑重构、校验和绘图。
- `gem_exp.ipynb`：GEM电源数据读取及候选电网节点匹配。
- `load_exp.ipynb`：省级逐时负荷的人口栅格下推与变电站节点映射。
- `cf_exp.ipynb`：基于ERA5和atlite生成逐小时网格风光水资源可用率。
- `uc_exp.ipynb`：整合OSM、GEM和负荷结果的一日PyPSA机组组合demo。
- `LOAD_WEIGHTING.md`：行业组合负荷权重、公式和候选数据源。
- `prepare_osm_power_network.sh`：从OSM PBF提取电力设施并生成GeoPackage。
- `src/raw/`：按稳定 `source_id` 下载、检查和定位原始数据。
- `src/standard/`：按dataset拆分的标准数据包，统一字段、单位和格式。
- `test_layers.ipynb`：分层模块的可执行接口示例。
- `data/`、`config/`、`templates/`、`outputs/`：输入、配置、模板和结果。

## 标准数据层

`config/standard_data.toml`记录dataset与source依赖、预处理脚本和可调流程参数；
`config/asset_type_mapping.csv`集中记录GEM/DOE到标准`type/technology`的有序
映射规则。公共
dataset ID固定为`spatial`、`network`、`generation`、`storage`、
`parameter`、`load`、`population`和`resource`。

```python
from src.standard import StandardDataManager

standard_data = StandardDataManager()
standard_data.check()
generation = standard_data.build("generation")
network = standard_data.load("network")
```

实体dataset使用GeoParquet；`network`返回包含`nodes`和`branches`的
`NetworkData`；`parameter`使用Parquet；连续时空数据使用xarray和NetCDF。
所有生成文件位于`outputs/standard/`。

| 标准输出 | 内容 |
|---|---|
| `network_nodes.parquet` | 电网节点GeoDataFrame |
| `network_branches.parquet` | 电网支路GeoDataFrame |
| `generation.parquet` | 发电设备GeoDataFrame |
| `storage.parquet` | 储能设备GeoDataFrame |
| `spatial.parquet` | 行政区等空间单元GeoDataFrame |
| `parameter.parquet` | 长表形式的技术经济参数 |
| `load.nc` | `time × uid`逐时负荷 |
| `population.nc` | `y × x`人口栅格 |

推荐通过`StandardDataManager.load(dataset_id)`读取：它会选择正确的读取器，并把
两个network文件恢复为一个`NetworkData`对象。Parquet/NetCDF也可以由
GeoPandas、pandas或xarray直接读取，适合脱离本项目模块的独立分析。由于
`voltage_kv`是Arrow list，直接读取实体GeoParquet时需要：

```python
import geopandas as gpd
import pandas as pd

generation = gpd.read_parquet(
    "outputs/standard/generation.parquet",
    to_pandas_kwargs={"types_mapper": pd.ArrowDtype},
)
```

标准`network`不设置最低电压阈值，只要求line/cable具备可解析的电压；算例所需的
220 kV、500 kV等阈值应在后续system-case层选择。原始PBF更新或GPKG缺失时，
manager根据TOML调用`prepare_osm_power_network.sh`重建派生文件。

分类规则按`source`分组并按`priority`升序执行，第一条匹配规则生效。新增
`type/technology`时，在`asset_type_mapping.csv`中增加一条具有唯一`rule_id`
和优先级的具体规则，并放在该来源的fallback规则之前；无需修改Python。输出中的
`classification_rule_id`可反查命中规则，`technology_raw`、`fuel_raw`等字段用于
抽样复核；fallback记录应作为人工校验重点。

## OSM电网拓扑

### 数据链

```text
data/osm/china-latest.osm.pbf
  -> prepare_osm_power_network.sh
  -> china-power-network.osm.pbf
  -> china-power-network.geojsonseq
  -> china-power-network.gpkg
  -> osm_exp.ipynb
```

```bash
cd paper-codes
./prepare_osm_power_network.sh
```

`osm_exp.ipynb` 当前读取 `china-power-network.gpkg/power_features`，线路阈值为
500 kV，变电站筛选阈值为220 kV。主要步骤为：

1. 读取并规范 `line/cable/substation/converter`，解析电压和AC/DC属性。
2. 将线路拆为segments和terminals，在米制投影中匹配substations。
3. 构建 `networkx.MultiGraph` 类型的 `grid_topology`。
4. junction只连接同电压线路；station可连接不同电压线路。
5. 构图后立即把 `node_province_name` 和
   `province_intersection_matched` 写入每个NetworkX node。
6. 展开GIS视图、生成 `grid_summary`/`grid_summary_check` 和三张地图。

核心对象：

- `grid_topology`：nodes与branches的唯一拓扑真值。
- `grid_nodes_gdf`：全部station/junction nodes及其坐标、省份和电压。
- `station_nodes_gdf`：station node点位及OSM元数据。
- `station_areas_gdf`：station的原始OSM点/面几何。
- `grid_branches_gdf`：branch几何、端点、长度、电压、AC/DC及两端省份。

`grid_topology` 的nodes/branches以及上述GIS视图均使用布尔属性
`in_largest_connected_graph` 标记是否属于最大连通图。

当前运行结果为6,419个nodes、7,742条branches、1,544个station nodes和
437个connected subgraphs；其中最大连通图包含5,343个nodes、7,080条
branches和1,482个station nodes。主要图片为：

- `outputs/china_transmission_network.png`
- `outputs/china_grid_topology.png`
- `outputs/china_largest_connected_graph.png`

OSM是社区维护的候选数据，不是经审定的中国电网模型。正式地图应遵守自然资源部
地图管理要求；电气分析还需补充线路阻抗、容量、变压器及已核实的连接关系。

## GEM电源匹配

`gem_exp.ipynb` 读取
`data/Global-Integrated-Power-March-2026-II.xlsx/Power facilities`，提取
`Country/area == "China"` 的中国大陆单位/阶段记录，并通过
`%run ./osm_exp.ipynb` 复用现有拓扑。

当前数据包含39,797条unit/phase记录和30,265个project/location ID；
Location accuracy只有 `exact`（12,977条）和 `approximate`（26,820条）。
station省份直接来自 `grid_topology`；GEM点另行与省界空间连接。
匹配前先把拓扑及GIS视图限制在最大连通图，再删除3个
`province_intersection_matched == False` 的station及其6条关联branches。
最终用于匹配的拓扑包含5,340个nodes、7,074条branches和1,479个station
nodes，并保持连通。
`mapping_scope` 是唯一且互斥的映射分类：

1. `inside_station`：4条。
2. `same_province_near`：38,093条。
3. `same_province_distant`：1,325条。
4. `national_fallback_near`：365条。
5. `national_fallback_distant`：10条。

near/distant由 `candidate_distance_km=100` 划分；Location accuracy保留为
独立源数据字段。默认模型纳入站内和near记录，排除两类distant记录。
这些结果是地理候选，不是已核实的并网站或接入电压。

GEM类型进一步把光伏与`solar_thermal`、陆上与`offshore_wind`分开。
`config/technical_economic_assumptions.csv`统一保存发电与储能设备的启停、
爬坡、最小出力和经济参数映射。生成的`gem_unit_parameters`
是一行一个GEM unit/phase的技术经济宽表；它没有geometry和并网节点。
`gem_grid_matches`保留空间信息和候选节点，UC中按`GEM unit/phase ID`一对一
合并为`uc_generation_units`。PyPSA成本主要是欧洲/国际口径，Dispa-SET启停值
是通用代理，不是中国机组实测值。

主要输出：

- `outputs/gem_grid_node_matches.csv`
- `outputs/gem_grid_node_capacity.csv`
- `outputs/gem_distant_grid_connections.csv`
- `outputs/gem_grid_mapping_summary.csv`
- `outputs/gem_grid_connection_candidates.png`

## 负荷映射

`load_exp.ipynb`读取Scientific Data/Figshare发布的2015-2024年31省逐时合成
负荷，并使用WorldPop 2020中国1 km人口计数栅格构造省内空间权重。默认聚合为
约30 km网格，再将每个网格分配到同省最近的、位于最大连通图且已匹配省份的
station node；变电站省份及经纬度直接复用`osm_exp.ipynb`的结果。

核心对象为`provincial_hourly_load`、`grid_load_weights_gdf`、
`station_load_weights`和`station_hourly_load`。输出包括网格映射GeoPackage、
静态节点权重CSV、逐时节点负荷Parquet，以及北京、山东、浙江和新疆的省级原始
负荷与站点堆叠负荷对比图。省内人口权重及全国逐时总量均做守恒检查。人口比例法
不能识别工业、自备电厂、牵引负荷和不同电压等级负荷，正式模型应进一步叠加
夜间灯光、企业/工业用地及分行业电量和典型曲线。

人口、夜间灯光、工业企业/工业用地的组合权重公式，两套城市级用电栅格数据及
守恒校正方法集中见`LOAD_WEIGHTING.md`。

## PyPSA一日机组组合

`uc_exp.ipynb`顺序执行`osm_exp.ipynb`、`gem_exp.ipynb`和`load_exp.ipynb`，
再把结果整理为PyPSA的Bus、Line、Link、Generator、Load和StorageUnit表。
AC支路使用DC潮流，DC支路使用双向Link；`config/uc_line_assumptions.csv`
按电压等级给出单位长度R/X/C和每回额定容量的通用代理值。

当前demo使用2024-08-01负荷、固定风光水资源可用率和当前GEM `operating`状态。
参考IEEE TPWRS综述中的基础UC结构，保留成本、最小出力、启停转换、最小开停机
时间、节点平衡及基态线路约束，不含备用、N-1或N-k。为控制全国MILP规模，每类
仅将容量最大的5个符合`technical_committable`规则的站点组合设为committable；
传统火电的可用上限默认为1，不使用固定容量因子。爬坡字段保留，但当前配置
`uc_enforce_ramp_constraints=False`，因为候选并网和站点聚合后的等效机组不满足
单机爬坡假设。

运行`cf_exp.ipynb`生成`outputs/capacity_factors_YYYY.nc`后，可把
`uc_resource_profile_mode`设为`cf_file`。UC将按照接入bus坐标从
`time × y × x`网格采样风电、光伏和水电逐时`p_max_pu`。ERA5下载需要CDS账号、
数据许可和`~/.cdsapirc`；没有本地cutout时notebook只展示准备说明，不伪造数据。

已验证算例包含5,340个buses、7,074条branches、1,298个loads、5,233个
generators和108个storage units，HiGHS返回optimal。日负荷30,217.94 GWh，
其中弃负荷464.80 GWh（1.54%）；该值主要是通用线路容量及候选并网造成的模型
瓶颈，不能视为真实系统缺电。输出为：

- `outputs/uc_network_2024-08-01.nc`
- `outputs/uc_dispatch_by_type_2024-08-01.csv`
- `outputs/uc_commitment_status_2024-08-01.csv`
- `outputs/uc_dispatch_2024-08-01.png`

## 环境与参考

固定依赖见 `requirements.txt`。负荷空间处理新增Rasterio，逐时节点负荷使用
PyArrow/Parquet保存。

- [GEM Global Integrated Power Tracker](https://globalenergymonitor.org/projects/global-integrated-power-tracker#get-the-latest)
- [Geofabrik China extract](https://download.geofabrik.de/asia/china.html)
- [OSM power tagging](https://wiki.openstreetmap.org/wiki/Key:power)
- [Osmium Tool manual](https://osmcode.org/osmium-tool/manual.html)
- [GDAL ogr2ogr](https://gdal.org/programs/ogr2ogr.html)
- [GeoPandas documentation](https://geopandas.org/en/stable/docs.html)
- [NetworkX MultiGraph](https://networkx.org/documentation/stable/reference/classes/multigraph.html)
- [中国标准地图服务](https://bzdt.ch.mnr.gov.cn/)
- [中国省级逐时负荷数据论文](https://doi.org/10.1038/s41597-026-07327-8)
- [中国省级逐时负荷数据](https://doi.org/10.6084/m9.figshare.29832701)
- [WorldPop中国人口栅格](https://hub.worldpop.org/geodata/summary?id=29818)
- [280城市1 km月度用电栅格](https://doi.org/10.6084/m9.figshare.25398559.v1)
- [296城市日度/月度用电数据](https://doi.org/10.1038/s41597-025-06256-2)
- [PyPSA technology-data](https://github.com/PyPSA/technology-data)
- [Dispa-SET输入数据](https://www.dispaset.eu/en/latest/data.html)
- [NREL 2024 ATB光热参数](https://atb.nrel.gov/electricity/2024/concentrating_solar_power)
- [PyPSA unit commitment](https://docs.pypsa.org/latest/examples/unit-commitment/)
- [PyPSA standard line types](https://docs.pypsa.org/v1.0.2/user-guide/components/line-types/)
- [HYPERSIM典型架空线参数](https://opal-rt.atlassian.net/wiki/spaces/PDOCHS/pages/150307736)
- [ERA5逐小时单层数据](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels)
- [atlite文档](https://atlite.readthedocs.io/)
- [Global Wind Atlas](https://globalwindatlas.info/)
- [Global Solar Atlas](https://globalsolaratlas.info/)
