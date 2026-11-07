# GridX（网探）

GridX 将中国电力系统开放数据转换为可检查、可追溯的电力系统算例，并通过 PyPSA
运行聚合机组的连续经济调度（UC/ED）。它覆盖数据获取、标准化、时空映射、算例组装、
生产模拟、地图与只读网站展示。当前应用不包含逐机组二进制启停或 N-1/N-k 安全约束。

## 从哪里开始

- **第一次了解项目：** 先看下方五层架构与快速开始，再查看处理原理。
- **已有生成数据：** 用 manager 的 `check()` 检查，再用 `load()` 读取；无需重新下载或求解。
- **希望复现实验：** 从 `notebooks/test_layers.ipynb` 开始，各专题 notebook 用于深入探索。
- **开发或修改代码：** 先阅读 [AGENTS.md](AGENTS.md)，其中集中保存构建与编程要求。

## 五层架构与数据流

```text
raw → standard → mapping → case → application
原始文件   规范数据    时空映射    系统算例    模型与结果
```

| 层 / 路径 | 作用 | 主要输出 |
|---|---|---|
| `src/raw/` | 按源目录获取并校验文件，不解释业务字段 | 原始文件、可用性报告 |
| `src/standard/` | 适配异构字段、分类、标识与数据结构 | `StandardData`、`StandardNetwork` |
| `src/mapping/` | 统一空间单元、时间轴及母线挂接 | `MappedData`，包括参数表 |
| `src/case/` | 选择范围、重挂接、匹配参数、聚合与校验 | 后端无关的 `PowerSystemCase` |
| `src/app/` | 构建目标与约束、求解并整理结果 | `UnitCommitmentResult` |

层间传递显式对象。case 只需完整 mapped data；应用只需 case。共享的
`src/visualization/` 管理绘图入口、语言、字体、投影与图例；`src/web/` 展示离线生成载荷。
空间单元用于索引和汇总，共处一个 cell 不代表电气连通，电气连通由设备端点决定。
资源空间分辨率、电气网络分辨率、机组聚合粒度、时间与场景口径可分别选择；更改后需要
重建受影响的映射或算例。代表日、不等步长等设想不应视为当前已经实现的应用功能。

## 安装与快速开始

当前推荐 **64 位 Python 3.13**（开发环境为 3.13.11），macOS 或 Windows。
Python 3.11 是代码使用 `tomllib` 的最低语法/标准库门槛，不代表本项目已验证其依赖组合。
PyPSA 后端契约要求 **1.2.x**，依赖文件固定为 1.2.4。Windows 安装尚未实机验证。

先安装 [Miniconda](https://docs.conda.io/en/latest/miniconda.html) 和
[Git](https://git-scm.com/downloads)，Mac 使用 Terminal，Windows 使用 Miniconda Prompt。
以下命令两者通用；也可下载仓库 ZIP，解压后进入包含 `requirements.txt` 的目录。

```bash
git clone https://github.com/yibaoj/GridX.git
cd GridX
conda create -n env-py313 python=3.13 pip -y
conda activate env-py313
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
python -c "from src.case import PowerSystemCaseManager; import pypsa; print(pypsa.__version__)"
python -m ipykernel install --user --name env-py313 --display-name "GridX (Python 3.13)"
jupyter lab
```

在 Jupyter 中选择 **GridX (Python 3.13)** kernel，打开 `notebooks/test_layers.ipynb`。
当前项目从源码目录运行，尚无 `pyproject.toml`/安装包入口；无需执行 `pip install .`。
`requirements.txt` 固定 Python 直接依赖，含 notebook 工具；它不是跨平台完整锁文件。
建议新建干净环境，不要对已有 Conda GIS 环境反复用 pip 覆盖安装。

安装 Python 包不包含原始数据、下载许可和系统命令行工具：

- **已有预处理结果：** 可直接按下方接口加载与分析。
- **从 OSM PBF 开始：** `prepare_osm_power_network.sh` 另需 Bash、`osmium` 和
  `ogr2ogr`（GDAL）。可在单独的工具环境执行
  `conda create -n gridx-osm -c conda-forge --strict-channel-priority osmium-tool gdal -y`，
  在 Bash 中设置 `CONDA_ENV_NAME=gridx-osm` 再运行该脚本；Windows 的此预处理步骤建议使用
  WSL，在 WSL 内安装 Conda 和工具，不能直接在原生 Prompt 运行 `.sh`。
- **ERA5：** 需接受 CDS 数据许可并按源目录要求配置 `~/.cdsapirc`。

若 pip 找不到某个固定版本或该平台的二进制 wheel，先核对 Python/CPU 架构；
不要在尚未验证时直接删掉全部版本约束。[GeoPandas 安装说明](https://geopandas.org/en/stable/getting_started/install.html)
提供了 GIS 二进制依赖和 Conda 替代安装建议。

下面的 Python 示例从 **GridX 仓库根目录**运行；输入数据就绪后再构建和求解。

```python
from src.raw import RawDataManager
from src.standard import StandardDataManager
from src.mapping import SpatiotemporalMappingManager
from src.case import PowerSystemCaseManager
from src.app import UnitCommitmentApplication

raw = RawDataManager()
print(raw.check())                         # 查看缺少哪些来源
# raw.prepare()                           # 准备来源；部分来源需按报告人工获取
standard = StandardDataManager("config/standard_data.toml", raw_data=raw)
print(standard.check())
# 原始输入就绪后，依次执行：
standard.build()
mapping = SpatiotemporalMappingManager("config/mapping.toml", standard)
mapping.build()
case_manager = PowerSystemCaseManager("config/case.toml", mapped_data=mapping)
case_manager.build()
case = case_manager.load()
print(case.validation)                    # 先检查参数和数据覆盖
network = case.to_pypsa(strict=True)        # 必需参数存在缺口时拒绝转换
# 修复 validation 中的缺口后再运行：
application = UnitCommitmentApplication(case, "config/uc.toml")
# result = application.run()
# result = application.load()              # 只分析已有结果
```

`build()` 返回报告，`load()` 返回数据对象。已有输出默认复用；修改 case 配置后用
`case_manager.build(overwrite=True)` 重建。仅检查网络可用 `case_manager.load("network")`，
母线数量为 `len(case.network.bus.data)`。完整 `load()` 还会打开负荷与资源时序。

## 配置与输出速查

| 文件 / 目录 | 用途 |
|---|---|
| `config/raw_data_sources.csv` | 来源 URL、版本、规范本地路径及获取选项 |
| `config/standard_data.toml` | 标准 dataset、source adapter 与参数质量排序 |
| `config/class_mapping.csv` | 资产分类规则 |
| `config/mapping.toml` | 空间单元、时间对齐、负荷下推与母线匹配 |
| `config/case.toml` | 电压、状态、容量、聚合、时段及后端选择 |
| `config/pypsa_parameter_manifest.toml` | 参数单位、后端属性、转换和 fallback 契约 |
| `config/uc.toml` | 应用时间范围、连续模式、滚动窗口与求解器 |
| `config/plot.toml`、`config/web.toml` | 绘图和网站输出配置 |
| `data/` | 原始输入及配置声明的派生预处理缓存 |
| `outputs/{standard,mapping,case}/` | 各层持久化数据与校验结果 |
| `outputs/app/uc/` | 调度时序和求解摘要 |
| `outputs/web/` | 网站只读载荷 |

## 各层接口与处理原理

## 原始数据层

`source_id`是稳定的数据源标识。`local_path`在下载前定义，是raw manager唯一认可的
规范保存位置；`options_json`保存特定source的下载或解析参数。source属于哪个标准
dataset只在`standard_data.toml`中通过`source_ids`配置，raw catalog不重复记录归属。

```python
from src.raw import RawDataManager

raw_data = RawDataManager()
raw_data.check()
raw_data.prepare("era5_china_2024")
era5_file = raw_data.get_file("era5_china_2024")
```

`available`仅在规范路径文件通过基本校验和checksum时为true；`status`解释不可用原因。
`detected_path`只检查规范路径所在目录及`data/`根目录下与`remote_file_name`完全同名的
候选文件，`prepare()`可将唯一候选移动到规范路径。可直接下载的source由`prepare()`
写入规范路径；需要许可或人工操作的source会返回状态和下载说明。OSM原始PBF通过以下
脚本生成带OSM ID的GeoPackage：

```bash
./config/prepare_osm_power_network.sh \
  data/osm/china-latest.osm.pbf \
  data/osm/china-power-network
```

ERA5下载需要接受CDS许可并配置`~/.cdsapirc`。年份、范围和特征由
`raw_data_sources.csv`的`options_json`控制。

## 标准数据层

固定dataset ID为`spatial`、`network`、`generator`、`storage`、`parameter`、
`load`、`population`和`resource`。实体数据使用GeoParquet，连续时空数据使用
xarray/NetCDF，network使用包含`bus`、`branch`、`transformer`和`converter`的
`StandardNetwork`。

```python
from src.standard import StandardDataManager

standard_data = StandardDataManager("config/standard_data.toml")
standard_data.check()
build_report = standard_data.build("network")
network = standard_data.load("network")
generator = standard_data.load("generator")
all_standard_data = standard_data.load()

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
- `network_model.py`：电压推断、节点清洗，以及四类标准电气对象的构造和校验。

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
# Manager和完整数据对象使用同一个接口。
figure = standard_manager.plot("network")
same_figure = standard_data.plot("network")
figures = standard_data.plot("resource", year=2024)
english = standard_data.plot("network", language="en")
```

plot接口只返回Figure，不主动display或保存。所有地图支持`map_crs`、
`spatial_levels`、`language="zh"/"en"`和`china_inset`。公共默认值在
`config/plot.toml`中配置；完整中国范围默认使用右下角南海插图，省份、其他国家或区域不会
误加该插图。每次调用传入的参数优先于配置文件。

## 时空映射层

```python
from src.mapping import SpatiotemporalMappingManager

mapping = SpatiotemporalMappingManager("config/mapping.toml", standard_data)
build_report = mapping.build()
mapped_data = mapping.load()
generator = mapping.load("generator")
mapping.schema(["network", "generator"])
```

`mapped_data`是包含全部八类dataset及mapping配置快照的`MappedData`。其中`parameter`从
standard层原样继承并写入`outputs/mapping/parameter.parquet`，因此case层只需要一个
完整的mapped-data入口。

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
辅助值为0的目标cell负荷严格为0；配置在
`uncovered_auxiliary_nearest_levels`中的正权重未覆盖cell先关联最近源区域，再参与该源
区域的归一化；其他未覆盖cell保留NaN并进入case validation。代码不根据
`marine_zone`标签统一清零。
全部结果写入`outputs/mapping/`，不会覆盖standard数据。

```python
figure = mapping_manager.plot("generator")
same_figure = mapped_data.plot("generator")
figures = mapped_data.plot("load", year=2024)
```

standard和mapping使用相同plot API、投影、配色和图例规则。

## 算例层

`PowerSystemCase`只依赖mapped data（其中已包含parameter），不读取standard或原始source。它按配置
筛选电压、状态和容量，重建最大连通子图，复用mapping接口重新挂接generator、storage
和load，解析参数，并按bus或cell聚合资产。

`case.toml`中`[generator]`和`[storage]`各自集中配置状态/容量筛选、母线匹配和聚合，
`[load]`配置负荷匹配和聚合。`mapping_method="geometry"`使用原始几何匹配母线
（`"cell"`改用cell中心）；`aggregate_by="bus"`按母线聚合（也可选`"cell"`）。
公共参数聚合规则仍放在`[aggregation]`，修改配置后用`build(overwrite=True)`重建算例。

```python
from src.case import PowerSystemCaseManager

case_manager = PowerSystemCaseManager(
    "config/case.toml",
    mapped_data=mapped_data,
)
build_report = case_manager.build()
case = case_manager.load()
generator = case_manager.load("generator")
# Reconstruct the complete case without rebuilding it.
case = case_manager.load()
case.validation
case.parameter_manifest()
pypsa_network = case.to_pypsa(strict=False)
```

四层管理器采用相同的离线接口约定：`prepare/build(..., overwrite=False)`先检查，仅补全
缺失结果，完成后返回精简DataFrame报告；`overwrite=True`强制重建；`load()`或
`get_file()`负责读取持久化结果。mapping会自动重建受上游变化影响的依赖闭包；case是
一个必须内部一致的整体，任一组成文件缺失时会重建完整case，而不是混用不同批次文件。

`case.network`包含bus、branch、transformer和converter；每个静态组件均提供`.data`、
`.parameter`和`.membership`。参数表保留`uid/name/group/value/unit`、命中规则和source
溯源。`config/pypsa_parameter_manifest.toml`是仅在PyPSA转换阶段使用的参数
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
figure = case_manager.plot("network")
same_figure = case.plot("network")
```

generator/storage饼图按bus汇总，load圆面积表示bus负荷；绘图取bus geometry的代表点，
保留原始几何不变。负荷图仅显示正负荷bus，不保证每个候选station bus都有负荷。

三层绘图统一由`src.visualization`配置和分派，manager和数据对象都使用
`plot(dataset_id, **kwargs)`。`visualization`管理语言、字体、投影和公共入口；standard准备
标准对象和基础地图，mapping增加cell聚合与饼图，case只适配算例对象并复用mapping绘图。
`case_manager`会缓存完整case，
避免连续绘图时重复打开大体量时序文件。Notebook展示后应调用`plt.close(figure)`；不用完整case
时可调用`case_manager.close()`释放NetCDF句柄。

## 应用层

当前`src/app/uc/`使用PyPSA求解聚合机组的连续UC/ED。它不重新读取原始数据，也不修改
`PowerSystemCase`。时间范围由case配置及`run(start=..., end=...)`共同限定；运行图可再按
`admin_uids`或`spatial_uids`限定空间范围。

```python
from src.app import UnitCommitmentApplication

application = UnitCommitmentApplication(case, "config/uc.toml")
formulation = application.list()
result = application.run()
saved_result = application.load()
realized_formulation = application.list(active_only=True)
figure = application.plot(
    start="2024-07-15 00:00",
    end="2024-07-15 23:00",
)
```

case的七类公共数据写入唯一目录`outputs/case/`；`load(dataset_id)`可独立读取，`load()`恢复完整
`PowerSystemCase`。case和application的plot接口均只返回Figure，不自动保存图片。

`application.list()`按sets、parameters、variables、objective、系统约束和设备约束顺序
返回LaTeX符号，并标记active状态；求解后再次调用还会对照Linopy中实际存在的变量和
约束。当前`continuous`模式不激活status/startup/shutdown二进制变量及最小开停机约束。
`strict_case=true`会拒绝required参数使用manifest fallback；`strict_case=false`仅适合
流程验证。UC完成后，逐generator、storage、load、AC line、transformer和link的原始时序
写入`outputs/app/uc/timeseries.nc`，求解摘要写入`summary.json`。保存前会检查有限值、
发电非负和储能功率上限；未通过检查的求解中间量不会覆盖已有结果。
`UnitCommitmentResult`同时持有输入case、内存中的PyPSA network、带UID和单位的xarray
时序、求解状态及摘要；从磁盘恢复时network不再反序列化，但绘图和结果分析所需的调度、
SOC、潮流、可用的启停变量和节点边际电价均保存在NetCDF中。
`solve_mode="rolling_horizon"`按配置窗口顺序求解并传递储能SOC。500 kV+全国网络适合
算例检查，但日/周交互展示应使用已说明分辨率的应用算例；实际电压阈值、时间范围和求解窗口以当前配置为准。

## Notebook与应用

- `notebooks/osm_exp.ipynb`：OSM GIS和拓扑探索验证。
- `notebooks/gem_exp.ipynb`：GEM电源、参数和候选station映射。
- `notebooks/load_exp.ipynb`：省级逐时负荷、人口权重和station负荷。
- `notebooks/cf_exp.ipynb`：ERA5风光水资源可用率。
- `notebooks/uc_exp.ipynb`：PyPSA一日UC demo，不含N-1/N-k安全约束。
- `notebooks/test_layers.ipynb`：五层接口、case 到 PyPSA 的映射及实际优化模型检查。

GridX（网探）网站只读取`outputs/web/`中的离线绘图数据。先调用与后端plot共享的准备
函数生成压缩GeoJSON和图例元数据，再启动只读服务：

```bash
conda run -n env-py313 python -m src.web.build --config config/web.toml
conda run -n env-py313 python -m src.web.app
```

网站请求期间不再启动GeoPandas/Xarray聚合；预压缩文件由HTTP服务直接发送。访问
`http://127.0.0.1:8765`。

`outputs/web/manifest.json.gz`描述可用层和资源子类，`boundary/common.json.gz`保存共享背景边界，
`layers/{standard,mapping,case}/`按dataset惰性加载地图载荷，`application/uc.json.gz`
保存生产模拟图所需的时序。`src/web/build.py`直接复用后端plot模块中的
`prepare_*`函数，而不是另写统计口径；压缩、几何简化和分层文件切分均在离线构建期完成。
case层resource与mapping层相同，服务端直接复用mapping载荷，不生成重复文件。

## 重要限制

- OSM是社区维护的候选网络，不是经审定的中国电网模型。
- GEM到station的匹配是地理候选，不代表已核实并网点和接入电压。
- ERA5径流代理不能直接替代电站级水文模型。
- 通用技术经济参数、线路参数和燃料价格必须在具体case中进行地区与年份校核。
- 当前transformer/converter和部分generator/storage参数覆盖不足，正式仿真应采用
  `strict=True`并先解决`case.validation`中的失败项。
- 正式地图应遵守自然资源部地图管理要求。

## 负荷空间下推：基线与扩展方案

以下保留原负荷研究说明中的公式、候选数据和实施顺序。人口/辅助量守恒下推是现有基线；
行业组合、城市电量校准和动态权重是扩展方案，不代表标准流程已实现。探索 notebook 的
WorldPop 单元与模块化 mapping 的目标 cell 可能不同，实际口径以配置及输出元数据为准。

### 1. 目的与当前基线

`notebooks/load_exp.ipynb`读取2015-2024年31省逐时合成负荷，并把省级负荷下推到
WorldPop空间单元，再映射到同省最近的OSM变电站。当前人口权重为：

\[
w^{pop}_{g,p} =
\frac{Pop_g}{\sum_{j\in p} Pop_j},
\qquad
L_{g,t}=L_{p,t}w^{pop}_{g,p}
\]

其中，\(g\)为省\(p\)内网格，\(t\)为小时。该方法透明且逐时守恒，但所有网格
在全年使用固定份额，容易低估人口少、工业或商业用电高的地区。

### 2. 分行业组合权重

建议把居民、工业、商业及其他负荷分别构造空间代理：

\[
q_{g,t}=\sum_{s\in S}E_{p,s}h_{s,t}z_{g,s}
\]

\[
L_{g,t}=L_{p,t}
\frac{q_{g,t}}{\sum_{j\in p}q_{j,t}}
\]

- \(E_{p,s}\)：省\(p\)行业\(s\)的年/月度用电量或占比；
- \(h_{s,t}\)：行业典型逐时负荷曲线，按研究时段归一化；
- \(z_{g,s}\)：行业空间代理，在省内按行业归一化；
- 分母使任意小时都满足\(\sum_{g\in p}L_{g,t}=L_{p,t}\)。

推荐空间代理：

| 行业 | 主要代理 | 辅助代理 |
|---|---|---|
| 居民 | WorldPop人口 | 住宅建成区、居民POI |
| 商业/公共服务 | VIIRS夜间灯光 | 商业建成区、第三产业企业和POI |
| 工业 | 工业企业数量或制造业企业网格 | OSM工业用地、工业园区、工业夜间灯光 |
| 农业及其他 | 农业用地、乡村人口 | 行业统计和典型曲线 |

为减弱极端亮度、企业注册地址聚集和OSM漏绘，可对代理采用
`log1p`或分位数截尾，再在省内归一化。例如：

\[
z_{g,ind} =
\operatorname{normalize}_p
\left[
\alpha \log(1+Firm^{ind}_g)
+\beta \sqrt{Area^{ind}_g}
+\gamma \log(1+NTL_g)
\right]
\]

系数不宜凭经验固定。优先使用已有1 km用电栅格或市级行业电量拟合；数据不足时
进行人口法、人口+夜光法和完整行业法三组敏感性分析。

### 3. 可用原始数据

#### 3.1 省级逐时总量

- [2015-2024年中国省级逐时负荷数据论文](https://doi.org/10.1038/s41597-026-07327-8)
- [Figshare工作簿](https://doi.org/10.6084/m9.figshare.29832701)

工作簿中每年对应`YYYY load curve`，单位为每小时间隔的GWh。它提供省级时间
边界条件，不提供省内空间结构或行业曲线。

#### 3.2 人口、夜间灯光和土地利用

- [WorldPop中国人口栅格](https://hub.worldpop.org/geodata/summary?id=29818)：
  约1 km人口计数，用于居民负荷基线。
- [VIIRS Nighttime Lights](https://eogdata.mines.edu/products/vnl/)：
  约500 m月度/年度夜间辐亮度。使用月度产品时必须结合无云观测次数，不能把
  缺测像元直接视为零。
- [ESA WorldCover](https://esa-worldcover.org/en/data-access)：
  10 m全球土地覆盖，可识别建成区，但不能直接区分住宅、商业与工业。
- [OSM `landuse=industrial`](https://wiki.openstreetmap.org/wiki/Tag:landuse%3Dindustrial)：
  可计算工业用地面积；不同城市的完整度不一致，应作为辅助变量。

#### 3.3 企业和经济活动

- [中国网格企业数据GED](https://doi.org/10.1038/s41597-020-00792-9)：
  基于约2550万条企业注册记录，提供2005-2015年、0.01度网格的企业总数、状态
  和行业分类。它能区分第二、第三产业空间结构，但年份较旧、注册地址不一定等于
  实际生产地址，且缺少企业规模和用电量。
- 省级/城市分行业电量可来自中国电力统计年鉴及地方统计年鉴，用于校准
  \(E_{p,s}\)，而不直接用于网格定位。

### 4. 两套城市用电数据

#### 4.1 280城市1 km月度用电栅格

- [数据下载与说明](https://doi.org/10.6084/m9.figshare.25398559)
- 覆盖2012年4月至2019年12月，共93个月；
- GeoTIFF格式、Albers等面积投影、1 km网格，覆盖280个城市。

这是当前空间下推最有帮助的数据。推荐用途：

1. 在覆盖城市内直接用其2019年或多年同月平均空间份额替代纯人口份额；
2. 用2024年VIIRS、建成区和企业变化更新2019空间模板；
3. 用它训练人口、夜光、工业用地和企业变量到网格电量的模型；
4. 最终仍缩放到省级逐时负荷，确保逐时守恒。

限制是时间止于2019年，数据本身也是估计产品而非电网量测，不能当作2024年真实
变电站负荷。

#### 4.2 296城市日度/月度用电数据

- [论文与数据说明](https://doi.org/10.1038/s41597-025-06256-2)
- 覆盖2022年296个地级及以上城市；
- 提供日度和月度城市总量，并非城市内部空间栅格；
- 使用国家、省、市统计、VIIRS日度夜光和气象变量估计。

推荐把它用于“省-城市-网格”三级约束：

1. 省级逐时负荷先按城市月度/日度份额分解到城市；
2. 城市内部再使用1 km用电栅格或行业组合权重分解；
3. 小时尺度仍来自省级曲线与行业典型曲线。

它可改善省内城市间差异和日度季节变化，但不能单独确定城市内部网格位置，也不能
直接恢复小时级变电站负荷。

### 5. 推荐实施顺序

1. **基线：** WorldPop静态权重，完成逐时守恒和站点映射。
2. **空间增强：** 对280城市使用1 km月度用电栅格，其他地区使用人口+VIIRS。
3. **行业增强：** 加入GED行业企业、OSM工业用地、WorldCover建成区和省级
   分行业电量。
4. **时间增强：** 引入居民、工业、商业典型曲线；用296城市数据校准城市日/月
   份额。
5. **验证：** 对比市级年度/月度统计、变电站已知峰值或地方电网典型日，并报告
   不同权重方案下的节点负荷和线路潮流敏感性。

人口、夜光、企业和用电栅格均为代理数据。最终结果应标注为“空间下推的合成节点
负荷”，不能表述为电网企业公开的真实变电站量测。

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

## 按地域构建算例与边界交换

`config/case.toml` 的 `[spatial]` 控制地域筛选。`spatial_uids = []` 不筛选；
非空时支持 mapping cell 的 `spatial_uid` 或其 `admin_uid`，多个 ID 取并集。
例如山东省可设置 `spatial_uids = ["province_boundaries:370000"]`。
ID 必须存在于当前 mapping 输出；城市或其他层级未映射时需先调整 mapping 并重建，
不会从名称或省级数据推断城市边界。cell 还保留带 `admin_` 前缀的行政区描述与溯源字段。

```toml
[spatial]
spatial_uids = ["province_boundaries:370000"]
boundary_mode = "interface" # closed 删除外部连接；interface 构造边界交换

[spatial.interface]
direction = "import" # import / export，每个算例统一方向
power_min_pu = 0.0
power_max_pu = 1.0
energy_min_pu = 0.0
energy_max_pu = 1.0
marginal_cost_eur_per_mwh = 50.0
```

发电、储能按代表点落入所选 cell 并集筛选；负荷和资源按所选 cell 筛选。
网络先应用状态、电压条件，再选真实内部母线的最大连通分量，最后生成边界虚拟设备；
这些虚拟母线不会成为资产重新挂接的候选。被排除的内部母线数写入 `case.validation`。
`closed` 同样保留研究范围内的负荷，再按现有规则重挂接，因此是主网简化算例。

`case.interface` 与 generator/storage 相同，具有 `.data/.parameter/.membership`，
可用 `case_manager.load("interface")` 单独读取，保存到 `outputs/case/interface/`。
它记录原 branch UID、真实内部接入母线、虚拟外部母线、方向、额定容量及配置参数。
接口以跨界 branch 为单位，不把仅几何穿越区域的过境线路视为接入，也不做工程通道合并。
边界原线路不会同时作为 Line 与 Interface 重复添加。
额定容量从已匹配的规范参数计算并乘回路数，不读取后端 manifest；AC 的 MVA 额定值沿用线性模型中的
有功容量近似。未解析出有来源依据的容量时拒绝建立接口，不用默认容量冒充实测额定值。

PyPSA 中，进口 Link 为外部→内部，出口为内部→外部；外部 Generator 出力均非负，
进口 `sign=+1`，出口吸收设备 `sign=-1`。接口效率为 1，成本只计在 Link 上，单位 EUR/MWh；
正值表示交换费用，负值可表达出口收入。该等值控制交换功率，不保留被删除外部 AC 网络的 KVL 耦合。

功率限制为 `power_*_pu × rated_power_mw`；电量限制为
`energy_*_pu × rated_power_mw × 当前求解窗口小时数`。
`single` 对整个选定时段约束，`rolling_horizon` 对每个窗口独立约束，最后短窗口使用实际时长。
默认 `[0,1]` 表示在额定范围内可调；不是电量跨窗口累计预算。上下界均须在 `[0,1]` 内，
且功率与电量比例区间必须有交集。接口不支持双向交换，外部电源也不代表真实送端机组模型。

修改配置后使用 `case_manager.build(overwrite=True)` 重建。旧 case 缺少 Interface 持久化文件时，
`check()` 会报告不完整；直接加载旧完整 case 时仍允许 `interface=None`。

### 直流通道容量假设

`data/parameter/model_assumptions.csv` 中维护直流架空通道的
`p_nom_mw_per_circuit`（MW/circuit），与交流 `s_nom_mva_per_circuit`（MVA/circuit）分离。
当前 500/660/800/1100 kV 的通用双极容量假设分别为 3000/4000/8000/12000 MW，
通过 `selector_json` 中的电压和 DC 类型条件匹配，质量标记为 `generic`。
这些值参考 NEA 公布的工程实例，并推广为电压等级代理，不代表每条线路的已核实容量。
一回路按完整双极通道计，不能直接用于单极/单导线建模；多段线路或重复通道仍需核实。
该 CSV 是仓库维护的补充输入；修改后需重建 standard parameter、mapping parameter 和 case。
Interface 仍拒绝缺失容量的 fallback；普通后端转换中的 fallback 行为保持不变。
