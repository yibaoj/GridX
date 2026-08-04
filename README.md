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
- `src/mapping/`：统一空间单元、时间轴及电气节点映射。
- `src/web/`：面向standard/mapping结果的本地交互式数据网站。
- `test_layers.ipynb`：分层模块的可执行接口示例。
- `data/`、`config/`、`templates/`、`outputs/`：输入、配置、模板和结果。

`config/raw_data_sources.csv`中的`local_path`是下载前确定的`data/`内规范路径；
自动下载始终直接保存到该路径。`remote_file_name`记录上游原始文件名。对于手动
下载或已按原名保存的文件，可先放在`data/`或规范路径所在目录，再运行
`RawDataManager.prepare(source_id)`；若只发现一个同名候选，manager仅调整其
路径和文件名，不改动文件内容。`check()`以`local_path_mismatch`报告尚未归档的
文件；发现多个候选时不会自动选择。

风光资源的raw输入使用CDS的ERA5逐小时单层再分析数据。目录中的
`era5_china_2024`配置覆盖中国区域（73-136 E、18-54 N）和2024年全部8784个
小时，通过atlite按月串行请求`wind`、`influx`、`temperature`和`runoff`特征，
写入`data/capacity-factors/era5_china_2024.nc`。运行前须在CDS网页接受ERA5
许可并配置`~/.cdsapirc`。ERA5原始时间使用UTC；时区统一留给后续时空映射层。
随后执行：

```bash
conda install -n env-py313 -c conda-forge cfgrib eccodes
```

```python
from src.raw import RawDataManager

raw_data = RawDataManager()
raw_data.check("era5_china_2024")
era5_report = raw_data.prepare("era5_china_2024")
```

下载完成后raw层校验特征、逐小时时间轴和经纬度覆盖范围；容量因子转换仍由
后续`resource`标准化流程负责。其中`runoff`服务于现有水电可用率流程，并非
风电或光伏计算的必要输入。修改年份、范围或特征时，只修改
`config/raw_data_sources.csv`中该source的`options_json`。

## 标准数据层

`config/standard_data.toml`记录dataset与source依赖、预处理脚本和可调流程参数；
`config/class_mapping.csv`集中记录GEM/DOE到标准`class/subclass`的有序
映射规则。公共
dataset ID固定为`spatial`、`network`、`generation`、`storage`、
`parameter`、`load`、`population`和`resource`。

```python
from src.standard import StandardDataManager

standard_data = StandardDataManager()
standard_data.check()
generation = standard_data.build("generation")
network = standard_data.load("network")
generation_schema = generation.schema
network_schema = network.schema
selected_schemas = standard_data.schema(["generation", "storage"])
all_available_schemas = standard_data.schema()
```

`standard_data.plot(dataset_id)`返回质量检查图但不写文件。spatial展示陆地与海洋边界；
population使用连续色带；generation与storage按原始点位绘制，以颜色和marker区分
一级class、点大小表示容量，并分别给出class和容量尺度图例；network展示全部标准
nodes和branches。load与resource
按class分别绘图：不传`class_name`时返回`{class: Figure}`，传入后返回单个Figure。
parameter没有空间字段，因此保留参数覆盖矩阵，不虚构地理位置。地图在绘制阶段统一
转换到中国Albers等面积米制投影，保证距离比例正确，并使mapping层未裁剪的规则cell
显示为正方形；该操作不修改标准数据自身的存储CRS。plot接口只返回Figure，不主动
调用`display()`；notebook应显式执行`display(figure)`并随后`plt.close(figure)`。

```python
generation_figure = standard_data.plot("generation")
load_figures = standard_data.plot("load", year=2024)
onshore_figure = standard_data.plot(
    "resource", year=2024, class_name="onshore"
)
```

所有地图接口都接受`map_crs`。默认值为生成规则cell时使用的中国Albers等面积米制
投影；使用相同投影可使未裁剪的50 km cell保持正方形。完整中国省级spatial会自动
把南海诸岛放入右下角插图；`china_inset=False`可关闭。该插图判定依赖全国覆盖范围
和中国省级adcode，省级子集及其他国家或地区不会触发。

```python
china_aea = (
    "+proj=aea +lat_1=25 +lat_2=47 +lat_0=0 +lon_0=105 "
    "+datum=WGS84 +units=m +no_defs"
)
figure = standard_data.plot("spatial", map_crs=china_aea)

china_lambert = (
    "+proj=lcc +lat_1=25 +lat_2=47 +lat_0=0 +lon_0=105 "
    "+datum=WGS84 +units=m +no_defs"
)
figure = standard_data.plot("network", map_crs=china_lambert)

figure_without_inset = standard_data.plot("spatial", china_inset=False)
```

`schema(dataset_ids)`读取指定的一项或多项真实标准输出，`schema()`汇总所有当前
已生成的dataset。加载后的pandas/GeoPandas/xarray对象及`NetworkData`可直接通过
`.schema`查看。返回表格直接列出真实输出的DataFrame列，或xarray的dimensions、
coordinates、data variables和attributes，并以`required`和`description`标记
`schema.py`规定的必要内容；schema不会用上游定义虚构实际输出中不存在的字段。
所有标准dataset都必须具有`standard_dataset_id`和`crs`两个dataset级属性，
分别标识稳定的标准数据集类型及geometry采用的坐标参考系；它们不是逐行重复的列。
GeoDataFrame使用GeoParquet原生CRS和文件元数据，parameter将两项写入Parquet
元数据，xarray将两项写入NetCDF dataset attributes。Manager只读取和校验，
不在加载阶段补写。

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
| `load.nc` | `time × uid × class`逐时负荷 |
| `population.parquet` | 可配置源像元聚合尺度的人口网格GeoDataFrame |
| `resource.nc` | `time × uid × class`风光水资源可用率 |

`resource.nc`保留ERA5原始0.25度网格和UTC逐小时时间轴，`class`取值为
`onshore`、`offshore_fixed`、`utility_scale_pv`和`run_of_river`。
风电采用atlite的对数风廓线与NREL 5.5 MW陆上/15 MW海上风机功率曲线；光伏采用
最优固定倾角、增强Reindl辐照度分解和Huld晶硅组件模型；径流式水电采用24小时
平滑的ERA5局地径流，扣除年均径流10%的生态流量，并以可用径流75%分位数作为
设计流量。所有结果裁剪至`[0, 1] p.u.`。水电结果尚未考虑HydroBASINS上游汇流、
河道传播、电站水头和实测流量校准，因此是网格级径流式资源代理，不是电站级
可发电量。模型和参数均在`config/standard_data.toml`中可追溯、可修改。

```python
from src.standard import StandardDataManager

standard_data = StandardDataManager()
resource = standard_data.build("resource")
resource_schema = resource.schema
```

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
manager根据TOML调用`prepare_osm_power_network.sh`重建派生文件；GPKG路径由
`feature_output_prefix + ".gpkg"`唯一确定。

分类规则按`source`分组并按`priority`升序执行，第一条匹配规则生效。新增
`class/subclass`时，在`class_mapping.csv`中增加一条具有唯一`rule_id`
和优先级的具体规则，并放在该来源的fallback规则之前；无需修改Python。输出中的
`mapping_rule_id`可反查命中规则，`technology`、`fuel_raw`等原始字段用于
抽样复核；fallback记录应作为人工校验重点。

## 时空映射层

`config/mapping.toml`控制标准空间单元、空间边界裁剪、时区、空间重映射和节点匹配。
默认生成50 km正方形，并按`level_priority = ["province", "marine_zone"]`选择空间单元
及确定冲突优先级；不足5 km²的碎片只在相同`admin_uid`内并入中心最近的单元。
列表越靠前优先级越高，后续level会扣除与前序level的重叠区域。同一level内部不做
任意优先级裁切，新增来源前应先检查其内部及同级重叠。standard spatial使用`level`，
全部mapping输出统一使用`spatial_level`。
`kind="polygon"`和`polygon_file`可改用外部多边形单元；改用city时，standard spatial
必须先包含`level="city"`记录。

`spatial`可依赖多个raw source。每个源在`raw_data_sources.csv`的`options_json`中声明
`spatial_level`和`source_uid_field`，standardizer据此生成`level`；单个源包含多种
level时可改用`spatial_level_field`指定原始列，不会根据文件名或几何位置猜测。
当前`marine_zone`来自World Bank/ESMAP东亚与太平洋海上风电技术潜力
数据中`ISO_Ter1=CHN`的固定式、漂浮式区域并集；它是资源评估空间域，不代表法定领海
或主权边界。若取得正式海洋功能区或海上风电规划GIS，只需增加一个配置了
`spatial_level="marine_zone"`的新raw source，无需修改standardizer。

```python
from src.mapping import SpatiotemporalMappingManager
from src.standard import StandardDataManager

standard_data = StandardDataManager()
mapping = SpatiotemporalMappingManager(
    "config/mapping.toml",
    standard_data,
)
spatial = mapping.build_cells()
connected_network = mapping.build_network()
mapped_data = mapping.build()
mapping.check()

# Load one product without opening all mapping outputs.
generation = mapping.load("generation")
generation[["uid", "spatial_uid", "node_uid", "node_distance_km"]]

# Inspect schemas from materialized outputs.
mapping.schema(["generation", "network"])
generation.schema
```

`build_network()`只根据标准network的`from_uid/to_uid`返回最大连通子图，不依赖
空间单元。完整`build()`内部会依次调用`build_cells()`和`build_network()`，随后才
把连通网络的nodes映射到空间单元，并写出全部派生结果。`build()`与无参数`load()`都
返回同一结构的`MappingData`；前者重新计算并写盘，后者只从已有文件恢复。
公开的mapping ID与standard dataset命名一致，即`spatial`、`population`、`load`、
`resource`、`network`、`generation`和`storage`。通过容器名称区分数据阶段，例如
`standard_spatial = standard_data.load("spatial")`和
`mapped_spatial = mapped_data.spatial`。generation和storage直接在GeoDataFrame中
增加空间单元及节点映射列，load则增加沿`uid`定义的节点映射coordinates。network
组合最大连通网络的nodes、branches及支路到空间单元的多对多映射。

resource默认按其原始像元与全部standard cell的几何交集进行强度型面积加权，不在
映射层按resource class设置陆海掩膜；输出保留`spatial_level`，算例层仍需按设备类别
选择相容空间域（例如offshore wind使用`marine_zone`）。原始ERA5目前南界为18°N，
海洋区中18°N以南的cell会保持缺失值，除非扩大ERA5下载范围后重建resource。

负荷采用空间守恒映射：细源像元按相交面积升尺度，粗源区域默认按映射后人口权重
拆分，且每个源区域权重重新归一化。`spatial_mapping.load.method="linear"`按面积
拆分；`method="auxiliary"`时，由`auxiliary_dataset`选择standard dataset，并由
`auxiliary_value`选择其中的数值列。`auxiliary_must_be_finer`控制辅助源是否必须比
标准空间单元更细。
resource是intensive量，按源/目标面重叠面积加权。这里的`linear`表示面域线性权重，
不是对中心点做双线性插值。所有时序转换到配置时区，但不自动裁剪共同年份。

network先按`from_uid`和`to_uid`提取并验证最大连通子图。nodes、generation和
storage得到单一`spatial_uid`；跨越多个空间单元的branches保存在显式many-to-many
crosswalk中。资产与负荷可分别配置`geometry`或`cell`节点匹配方式，默认只
匹配station，并保留距离、两端admin UID和`node_same_admin`供质量检查。全部派生结果
写入`outputs/mapping/`，不会覆盖standard datasets。

mapping层采用与standard层完全相同的`plot(mapping_id, ...)`接口、浅灰中国底图、
class配色和连续色带。spatial展示裁剪后的cell边界；population、load和resource
展示cell值；generation与storage在cell中心绘制容量缩放的class占比饼图；network
展示最大连通子图，并高亮与nodes和branches关联的cells。连续色标统一放在左侧；
generation与storage同时展示class颜色图例和总容量圆圈尺度图例。

```python
mapped_generation_figure = mapping.plot("generation")
mapped_load_figures = mapping.plot("load", year=2024)
mapped_pv_figure = mapping.plot(
    "resource", year=2024, class_name="utility_scale_pv"
)
mapped_network_figure = mapping.plot("network")
```

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
./prepare_osm_power_network.sh \
  data/osm/china-latest.osm.pbf \
  data/osm/china-power-network
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

核心对象为`provincial_hourly_load`、`cell_load_weights_gdf`、
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

## 数据网站

`src/web/`使用只读Python数据接口和Leaflet地图展示mapping结果，不复制或修改
标准数据。图层、默认视角、500 kV网络筛选阈值和右侧更新流集中配置于
`config/web.toml`。启动命令为：

```bash
conda run -n env-py313 python -m src.web.app
```

浏览器访问`http://127.0.0.1:8765`。当前图层包括空间单元、人口、发电、储能和
高压网络；发电与储能在服务端按`spatial_uid`聚合后传输，避免浏览器加载全部原始
对象。

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
