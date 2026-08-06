# Power System Operations

本目录保存中国电力系统开放数据的获取、标准化、时空映射、可视化和算例实验代码。
代码统一在conda环境`env-py313`中运行：

```bash
conda activate env-py313
```

## 编程规则

1. 关键对象使用稳定且语义明确的名称；局部中间变量以`_`开头。
2. 重复处理集中为函数或模块，不在多个notebook复制实现。
3. `lines/substations`表示原始OSM对象，`segments/terminals/nodes`表示GIS处理中间对象；
   `bus/branch/transformer/converter`表示标准化后的电气网络。
4. 原始输入、配置和生成结果分别放在`data/`、`config/`和`outputs/`。
5. 修改后在`env-py313`中重建相关dataset并检查schema、守恒关系和拓扑完整性。

项目级Codex规则见`AGENTS.md`。

## 代码结构

| 路径 | 作用 |
|---|---|
| `src/raw/` | 下载、定位和校验原始source |
| `src/standard/` | 将异构source转换为稳定dataset |
| `src/mapping/` | 统一空间单元、时间轴及电气母线映射 |
| `src/case/` | 构建后端无关算例并适配PyPSA等求解框架 |
| `src/app/` | 基于PowerSystemCase运行UC/ED等应用并整理结果 |
| `src/web/` | 只读展示standard/mapping结果 |
| `config/raw_data_sources.csv` | 原始数据URL、规范本地路径和下载选项 |
| `config/standard_data.toml` | dataset依赖及标准化参数 |
| `config/class_mapping.csv` | generator/storage的有序分类规则 |
| `config/mapping.toml` | 空间单元、重映射和节点匹配参数 |
| `config/case.toml` | 算例筛选、聚合、时间和后端参数 |
| `test_layers.ipynb` | 各层公共API和绘图示例 |

`osm_exp.ipynb`、`gem_exp.ipynb`、`load_exp.ipynb`、`cf_exp.ipynb`和
`uc_exp.ipynb`保留探索过程与demo；可复用数据流程以`src/`模块为准。

## 原始数据层

`source_id`是稳定的数据源标识。`local_path`在下载前定义，是raw manager唯一认可的
规范保存位置；`options_json`保存特定source的下载或解析参数。

```python
from src.raw import RawDataManager

raw_data = RawDataManager()
raw_data.check()
raw_data.prepare("era5_china_2024")
```

可直接下载的source由`prepare()`写入规范路径；需要许可或人工操作的source会返回明确
状态和下载说明。OSM原始PBF通过以下脚本生成带OSM ID的GeoPackage：

```bash
./prepare_osm_power_network.sh \
  data/osm/china-latest.osm.pbf \
  data/osm/china-power-network
```

ERA5下载需要接受CDS许可并配置`~/.cdsapirc`。年份、范围和特征由
`raw_data_sources.csv`的`options_json`控制。

## 标准数据层

固定dataset ID为`spatial`、`network`、`generator`、`storage`、`parameter`、
`load`、`population`和`resource`。实体数据使用GeoParquet，连续时空数据使用
xarray/NetCDF，network使用包含`bus`、`branch`、`transformer`和`converter`的
`NetworkData`。

```python
from src.standard import StandardDataManager

standard_data = StandardDataManager("config/standard_data.toml")
standard_data.check()
network = standard_data.build("network")
generator = standard_data.load("generator")

network.schema
generator.schema
standard_data.schema(["network", "generator"])
```

schema基于真实输出生成，并校验必要字段、字段顺序、dtype、CRS和
`standard_dataset_id`。所有标准结果写入`outputs/standard/`。

### Network标准化顺序

1. 从GeoPackage读取并筛选OSM `line/cable/substation/converter/transformer`，保留
   source UID、状态和原始标签。
2. 解析每条原始line way的电压集合；缺失值依次从相邻已标记线路、最近线路几何和
   全局众数推断，并记录`voltage_assignment_method`和`voltage_inferred`。
3. 将多电压way拆成单电压系统；按照原始标签或可审计简化规则分配`circuits/cables`。
4. 在每个电压层识别共享OSM node、线路terminal以及容差内同电压terminal。
5. 将线路在terminal、同电压共享node、station和transformer处切分，生成单电压
   branch候选及其真实geometry、长度、AC/DC和地理端点UID。
6. 生成内部地理nodes。显式OSM设施归为`station`；其余拓扑节点归为`junction`，度为1的是
   `line_terminal`，度不小于2的是`same_voltage`。
7. 普通junction按电压生成独立UID。同位置不同电压junction不直接连接；匹配到station
   或transformer的不同电压branches共享设施UID。
8. station最终电压是原始station电压与已连接branches电压的并集；缺失且孤立的设施
   从最近已知节点推断。缺少原始substation类别时，按最大电压是否不低于
   `transmission_voltage_threshold_kv`（当前220 kV）判断transmission/distribution。
9. 仅合并geometry、class、subclass、status和voltage完全一致的内部nodes，改写branch
   候选端点并保留`merged_source_uids`；校验UID、端点、电压、节点度和设施来源。
10. 按每个地理node实际接入的`(voltage_kv, current_type)`组合生成单电压、单AC/DC
    `bus`，并把branch端点严格改写为匹配bus。多电压station的相邻电压bus按电压排序
    组成可审计推断链：AC-AC生成`transformer`，AC-DC生成无方向含义的
    `ac_dc_converter`；没有明确设备证据时不推断DC-DC转换设备。

实现按职责分为：

- `network.py`：OSM读取和拓扑编排。
- `network_voltage.py`：电压推断、多电压拆分及回路/导体分配。
- `network_nodes.py`：节点分类、去重和完整性校验。
- `network_electrical.py`：从内部地理nodes生成四类标准电气对象并校验端点一致性。

标准network不设置最低电压筛选；220/500 kV等算例范围留给case层。所有bus和branch
均为单电压且具有AC/DC属性，branch与两端bus的电压和电流类型必须完全一致。普通同位置
多电压junction互不直连；多电压station已展开为多个bus，并由推断的transformer或
converter连接。

相邻电压设备链来自OSM线路接入关系的简化推断，不等同于已核实的实际绕组或换流拓扑；
`connection_method="inferred_adjacent_voltage_chain"`和`inferred=true`保留该口径。

### Parameter

parameter采用长表，稳定参数名为`name`，`group`仅用于technical、economic、
environmental和others分组。`resolve()`按对象UID、dataset、class/subclass、状态、容量、
电压、时间、地区、场景、可选source priority和数据质量匹配；`match_rank`、
`match_result`和`match_info`分别记录最终规则序号、criterion及对应值或失败原因。
全局规则由`parameter.matching_rules`给出，rank从`-1 ineligible`到`7 ambiguous`；
并列候选冲突时不会静默选择。

每个原始参数源在`standard_data.toml`中声明`source_id`及表形adapter：`long_table`
处理一行一个参数的长表，`wide_table`把参数列展开成长表。分类优先采用源表已有字段，
否则使用公共`class_mapping.csv`；公共规则仍不足时，可为该source配置独立
`mapping_file`。adapter不内置特定数据源分类，也不存在处理顺序依赖。

`model_assumptions.csv`合并Dispa-SET、NREL及generic UC/SOC/VOLL补充假设；PyPSA
technology-data已经提供的参数不在该文件重复保存。所有source输出合并后按UID稳定排序。

```python
parameter = standard_data.load("parameter")
parameter.count()
parameter.validate()
parameter.matching_rules

resolved, candidates = parameter.resolve(
    generator.iloc[0],
    ["minimum_output_pu", "ramp_up_pu_per_hour"],
    dataset_id="generator",
    include_candidates=True,
)

# The same API accepts a DataFrame for batch resolution.
resolved = parameter.resolve(
    generator,
    ["minimum_output_pu", "ramp_up_pu_per_hour"],
    dataset_id="generator",
)

# Supply fields referenced by parameter.selector_json without changing the asset.
resolved = parameter.resolve(
    {"uid": "osm:branch:1", "class": "branch"},
    "r_ohm_per_km",
    dataset_id="network",
    selector_context={"current_type": "AC"},
)
```

`selector_context`只补充缺失的匹配字段；与资产已有字段冲突时直接报错。

### Resource

`resource.nc`保留ERA5原始网格和UTC逐小时时间轴。风电使用atlite风廓线和风机功率
曲线，光伏使用固定最优倾角、Reindl辐照度分解和Huld组件模型。`run_of_river`目前只是
局地径流代理，尚未包含上游汇流、河道传播、电站水头和实测流量校准。

### 绘图

```python
figure = standard_data.plot("network")
figures = standard_data.plot("resource", year=2024)
```

plot接口只返回Figure，不主动display或保存。所有地图支持`map_crs`、
`spatial_levels`和`china_inset`；完整中国范围自动使用南海插图。

## 时空映射层

```python
from src.mapping import SpatiotemporalMappingManager

mapping = SpatiotemporalMappingManager("config/mapping.toml", standard_data)
mapped_data = mapping.build()
mapping.check()
generator = mapping.load("generator")
mapping.schema(["network", "generator"])
```

`build()`完成以下工作：

1. 根据`cell.kind`生成规则方格、外部polygon或选定spatial levels组成的标准cell。
2. 按`level_priority`消除不同空间层重叠，并处理小面积碎片。
3. population按面积汇总；load按面积或辅助数据守恒分配；resource按相交面积进行
   intensive加权；所有时序转换到配置时区和时间步。
4. 根据branch、transformer和converter的`from_bus_uid/to_bus_uid`提取最大连通子图。
5. 把bus、branch、generator和storage映射到cell。
6. generator、storage和load按各自配置匹配电气bus。默认候选为
   `bus_subclasses=["station_bus"]`；同位置多bus按各自配置的
   `voltage_preference="high"`或`"low"`选择。

资产映射保留`bus_uid`、距离、空间单元、admin UID和同区域标志。当前流程会强制选择
候选station bus；正式case应增加最大距离规则，把异常远距离匹配设为unresolved。

load是extensive量，拆分后保持总量；resource是intensive量，使用面积加权平均。
`method="auxiliary"`使用映射至目标cell后的辅助值并在每个源区域内归一化。
全部结果写入`outputs/mapping/`，不会覆盖standard数据。

```python
figure = mapping.plot("generator")
figures = mapping.plot("load", year=2024)
```

standard和mapping使用相同plot API、投影、配色和图例规则。

## 算例层

`PowerSystemCase`只依赖mapped data和standard parameter，不读取原始source。它按配置
筛选电压、状态和容量，重建最大连通子图，复用mapping接口重新挂接generator、storage
和load，解析参数，并按bus或cell聚合资产。

```python
from src.case import PowerSystemCaseManager

case_manager = PowerSystemCaseManager(
    "config/case.toml",
    mapped_data=mapped_data,
    standard_data=standard_data,
)
case = case_manager.build()
case.validation
case.parameter_manifest()
pypsa_network = case.to_pypsa(strict=False)
```

`case.network`包含bus、branch、transformer和converter；每个静态组件均提供`.data`、
`.parameter`和`.membership`。参数表保留`uid/name/group/value/unit`、命中规则和source
溯源。`config/pypsa_parameter_manifest.toml`是validation和PyPSA adapter共享的唯一参数
契约，记录目标属性、case参数名、规范/允许单位、转换公式、fallback、required和适用
selector。`strict=True`拒绝required参数使用fallback；`strict=False`允许显式fallback并
在PyPSA `meta`中记录缺口。fallback不再分散在adapter代码或`case.toml`中。

PyPSA generator边际成本按`vom + fuel / efficiency + carbon_price *
co2_intensity / efficiency`组装，carrier细化到`class:subclass`。规划扩容开关和碳价在
`case.toml`配置；参数覆盖检查通过后，扩容与经济调度结果才具有完整经济含义。

当前按`bus/cell + class + subclass`聚合适用于连续ED、规划和聚类UC。聚合会丢失单机
二进制启停轨迹，启动/停机参数虽然保留，但在连续PyPSA generator中不会生效。

case绘图与standard/mapping保持相同API，只返回Figure：

```python
figure = case.plot(
    "network",
    spatial=standard_data.load("spatial"),
    map_crs=mapping.options["metric_crs"],
)
```

## 应用层

当前`src/app/uc/`使用PyPSA求解聚合机组的连续UC/ED。它不重新读取原始数据，也不修改
`PowerSystemCase`。时间范围由case配置及`run(start=..., end=...)`共同限定；运行图可再按
`admin_uids`或`spatial_uids`限定空间范围。

```python
from src.app import UnitCommitmentApplication

application = UnitCommitmentApplication(case, "config/uc.toml")
formulation = application.list()
result = application.run()
realized_formulation = application.list(active_only=True)
figure = result.plot(
    start="2024-07-15 00:00",
    end="2024-07-15 23:00",
)
```

`application.list()`按sets、parameters、variables、objective、系统约束和设备约束顺序
返回LaTeX符号，并标记active状态；求解后再次调用还会对照Linopy中实际存在的变量和
约束。当前`continuous`模式不激活status/startup/shutdown二进制变量及最小开停机约束。
`strict_case=true`会拒绝required参数使用manifest fallback；`strict_case=false`仅适合
流程验证。达到求解时限但已有可行解时，结果保留
`termination_condition="time_limit"`且`is_optimal=false`。

## Notebook与应用

- `osm_exp.ipynb`：OSM GIS和拓扑探索验证。
- `gem_exp.ipynb`：GEM电源、参数和候选station映射。
- `load_exp.ipynb`：省级逐时负荷、人口权重和station负荷。
- `cf_exp.ipynb`：ERA5风光水资源可用率。
- `uc_exp.ipynb`：PyPSA一日UC demo，不含N-1/N-k安全约束。
- `LOAD_WEIGHTING.md`：工业、人口和夜间灯光组合负荷权重方案。

网站使用只读数据接口：

```bash
conda run -n env-py313 python -m src.web.app
```

访问`http://127.0.0.1:8765`。

## 重要限制

- OSM是社区维护的候选网络，不是经审定的中国电网模型。
- GEM到station的匹配是地理候选，不代表已核实并网站和接入电压。
- ERA5径流代理不能直接替代电站级水文模型。
- 通用技术经济参数、线路参数和燃料价格必须在具体case中进行地区与年份校核。
- 当前transformer/converter和部分generator/storage参数覆盖不足，正式仿真应采用
  `strict=True`并先解决`case.validation`中的失败项。
- 正式地图应遵守自然资源部地图管理要求。

## 参考

- [GEM Global Integrated Power Tracker](https://globalenergymonitor.org/projects/global-integrated-power-tracker#get-the-latest)
- [Geofabrik China extract](https://download.geofabrik.de/asia/china.html)
- [OSM power tagging](https://wiki.openstreetmap.org/wiki/Key:power)
- [Osmium Tool](https://osmcode.org/osmium-tool/manual.html)
- [ERA5](https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels)
- [atlite](https://atlite.readthedocs.io/)
- [PyPSA technology-data](https://github.com/PyPSA/technology-data)
- [pandapower standard types](https://pandapower.readthedocs.io/en/latest/std_types/basic.html)
- [Dispa-SET](https://www.dispaset.eu/en/latest/data.html)
- [中国标准地图服务](https://bzdt.ch.mnr.gov.cn/)
