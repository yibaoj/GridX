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
| `src/web/` | 只读展示standard/mapping结果 |
| `config/raw_data_sources.csv` | 原始数据URL、规范本地路径和下载选项 |
| `config/standard_data.toml` | dataset依赖及标准化参数 |
| `config/class_mapping.csv` | generation/storage的有序分类规则 |
| `config/mapping.toml` | 空间单元、重映射和节点匹配参数 |
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

固定dataset ID为`spatial`、`network`、`generation`、`storage`、`parameter`、
`load`、`population`和`resource`。实体数据使用GeoParquet，连续时空数据使用
xarray/NetCDF，network使用包含`bus`、`branch`、`transformer`和`converter`的
`NetworkData`。

```python
from src.standard import StandardDataManager

standard_data = StandardDataManager("config/standard_data.toml")
standard_data.check()
network = standard_data.build("network")
generation = standard_data.load("generation")

network.schema
generation.schema
standard_data.schema(["network", "generation"])
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
    组成可审计推断链：AC-AC生成`transformer`，含DC的一对生成`converter`。

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
电压、时间、地区、场景、priority和数据质量匹配；最高优先级候选数值冲突时返回
`ambiguous`，不会静默选择。

```python
parameter = standard_data.load("parameter")
parameter.report()
parameter.validate()
parameter.matching_rules

resolved, candidates = parameter.resolve(
    generation.iloc[0],
    ["minimum_output_pu", "ramp_up_pu_per_hour"],
    dataset_id="generation",
    include_candidates=True,
)
```

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
generation = mapping.load("generation")
mapping.schema(["network", "generation"])
```

`build()`完成以下工作：

1. 根据`cell.kind`生成规则方格、外部polygon或选定spatial levels组成的标准cell。
2. 按`level_priority`消除不同空间层重叠，并处理小面积碎片。
3. population按面积汇总；load按面积或辅助数据守恒分配；resource按相交面积进行
   intensive加权；所有时序转换到配置时区和时间步。
4. 根据branch、transformer和converter的`from_bus_uid/to_bus_uid`提取最大连通子图。
5. 把bus、branch、generation和storage映射到cell。
6. generation、storage和load按各自配置匹配电气bus。默认候选为
   `bus_subclasses=["station_bus"]`；同位置多bus按各自配置的
   `voltage_preference="high"`或`"low"`选择。

资产映射保留`bus_uid`、距离、空间单元、admin UID和同区域标志。当前流程会强制选择
候选station bus；正式case应增加最大距离规则，把异常远距离匹配设为unresolved。

load是extensive量，拆分后保持总量；resource是intensive量，使用面积加权平均。
`method="auxiliary"`使用映射至目标cell后的辅助值并在每个源区域内归一化。
全部结果写入`outputs/mapping/`，不会覆盖standard数据。

```python
figure = mapping.plot("generation")
figures = mapping.plot("load", year=2024)
```

standard和mapping使用相同plot API、投影、配色和图例规则。

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
