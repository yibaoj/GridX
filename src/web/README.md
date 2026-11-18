# GridX 网页界面

网页展示已导出的电网地图、全国与省级指标、UC 调度结果和新闻。React 前端通过同源 `/api/` 读取版本化数据包。

## 编程结构

```text
src/web/
├── build.py           # 后端 → 网页数据的唯一导出适配入口
├── contract.py        # v1 数据格式与结构校验，使用 Python 标准库
├── data.py            # 从导出数据包读取 API 载荷
├── ui_data.py         # 从数据包计算首页、省级指标及缓存
├── app.py             # 只读 HTTP 路由与页面服务
├── check.py           # 运行中服务与本地数据的一致性检查
├── test_ui_data.py    # 数据契约、缓存和路由回归测试
├── frontend/          # React/TypeScript 页面、样式、测试与 Vite 配置
│   ├── App.tsx        # 页面导航，Overview / Explorer / Simulation / News
│   ├── data.ts        # API 请求与浏览器缓存
│   ├── contract.ts    # 浏览器端的数据版本检查
│   ├── types.ts       # 页面依赖的字段类型
│   └── map.worker.ts  # 后台读取地图数据并准备显示几何
└── legacy/            # 旧界面，继续通过 /legacy/ 访问
```

前端源码直接放在 `frontend/`，组件、样式及对应测试同目录。`package.json` 定义开发、测试与打包命令。

## 输出目录

```text
outputs/web/
├── data/              # 原 outputs/web 的离线业务数据包，由 build.py 生成
├── site/              # 原 outputs/web-ui 的前端 HTML、JS、CSS，由 Vite 生成
└── cache/             # 原 outputs/web-ui-data 的首页汇总缓存，服务启动时生成
```

`data/` 决定页面展示的内容，`site/` 决定页面界面，`cache/` 可由 `data/` 重建。
构建前端只清理 `site/`。导出数据先在临时目录中完成校验，再替换 `data/`，导出失败保留上一份数据。
更新数据时停止服务，导出完成后重启。服务启动会依据数据文件和汇总代码校验值复用或重建缓存。

## 安装与运行

在仓库根目录操作，已有数据已迁移至 `outputs/web/data/`。其他环境需先配置
`config/web.toml` 的 `[data].output_root = "outputs/web/data"`，准备导出数据。

```shell
cd src/web/frontend
npm ci
npm run build
cd ../../..
conda run -n env-py313 python -m src.web.app
```

打开 `http://127.0.0.1:8765/`。Python 提供构建页面和 API，前端使用本地行政边界，图表及地图库随构建打包。

数据导出需要标准层、映射层、Case 与已保存的 UC 结果齐全。

```shell
conda run -n env-py313 python -m src.web.build --config config/web.toml
```

`build.py` 是后端模型与网页之间的适配器。后端字段、对象或准备函数变化时，在这里继续转换成同一网页数据格式。
`[data]` 指定输入配置、地图年份、几何简化精度与输出目录，`[server]` 设置地址与端口。
`[site]`、`[map]`、`[[stages]]`、`[[layers]]` 提供 bootstrap 信息，前端也维护页面名称与导航。
新版新闻在 `frontend/News.tsx` 中维护，TOML 的 `[[news]]` 供旧界面使用。

前端开发运行 `npm run dev`，Vite 把 `/api` 代理到 `127.0.0.1:8765`。
`GRIDX_API_URL=http://127.0.0.1:9000 npm run dev` 可指定另一个已启动的 Python 服务。

## 前后端数据契约 v1

```text
后端数据 → build.py 适配与校验 → data/ → data.py 与 ui_data.py → /api → frontend/
```

HTTP 服务和页面依赖导出数据契约。`ui_data.py` 使用 Python 标准库，前端构建不导入后端模型。
`contract.py` 在导出和读取时校验载荷，`frontend/contract.ts` 拒绝不支持的数据版本。
新增可选字段兼容 v1，删除字段、改变含义或单位需要升级契约，并同步更新适配器和前端。
现有未标版本的数据包按 v1 兼容读取，新导出文件显式写入 `schema_version=1`。

| 数据文件 | API | 稳定内容 |
| --- | --- | --- |
| `manifest.json.gz` | `/api/bootstrap` 的 manifest | year、stages、layers、resource_classes |
| `boundary/common.json.gz` | `/api/boundary/{stage}` | GeoJSON FeatureCollection，地区 ID、名称、级别 |
| `layers/{stage}/{layer}.json.gz` | `/api/layers/{stage}/{layer}` | summary 与 geojson |
| `layers/{stage}/resource/{class}.json.gz` | `/api/layers/{stage}/resource/{class}` | 同一地图结构，Case 复用 Mapping 文件 |
| `application/uc.json.gz` | `/api/application/uc` | time、series、categories、summary |
| 服务生成的缓存 | `/api/overview`、`/api/regional-overview` | 全国、省级指标、来源校验值 |

地图几何使用 WGS84 经纬度。发电与储能的 `total_mw`、`breakdown` 使用 MW，网络 `voltage_kv` 使用 kV，
年度负荷 `value` 及 summary 声明 TWh，资源可用率使用 p.u.。人口 `raw_value` 为人数，
`value` 为显示使用的 `log10(人数+1)`。稳定 ID 使用 uid、location_uid 或 spatial_uid。
UC 每条 series 包含 id、kind、values，values 与 time 等长，功率单位 MW，储能充电系列为负值。
浏览器展示已有调度结果，UC 运行由应用层负责。

后端重构需运行契约测试并检查导出的字段与单位。校验负责发现不兼容，适配器负责维持兼容。
旧版数据可继续读取。缓存损坏可重建，导出文件缺失或不兼容时页面给出提示。

## 验证与部署

```shell
conda run -n env-py313 python -m unittest src.web.test_ui_data -v
cd src/web/frontend
npm test
npm run build
cd ../../..
conda run -n env-py313 python -m src.web.check
```

最后一项需要已启动的本地服务，比较 HTTP 数据和实际导出包，并验证首页指标来源。

公开域名可通过 Nginx/Caddy 转发到本机 Python 服务，页面和 `/api` 保持同源。
部署需准备 `site/`、`data/` 和服务配置，`cache/` 可在启动时重建。当前 HTTP 入口是开发服务，
正式部署需要配置 HTTPS、进程管理并评估生产服务框架。仅托管 `site/` 还需要提供对应 `/api`。
页面默认使用域名根路径，HTML 的同源 CSP 响应头由 Python 服务设置。
