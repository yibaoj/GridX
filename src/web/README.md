# GridX 网页界面

React、TypeScript、Vite、ECharts、MapLibre/deck.gl 与 Leaflet 构成网页界面。Python 服务提供页面和只读数据接口。界面包含数据总览、电力地图、运行模拟和新闻消息。

## 安装与启动

在仓库根目录确认已有项目生成的网页数据，默认位置是 `outputs/web/`。

```shell
cd src/web/frontend
npm ci
npm run build
cd ../../..
conda run -n env-py313 python -m src.web.app
```

打开 `http://127.0.0.1:8765/`。构建输出位于 `outputs/web-ui/`。原界面保留在 `/legacy/`。第一次启动会从本地导出文件准备首页缓存，后续启动复用经过来源校验的缓存。

如需重新生成数据，在已有标准层、映射层、算例及模拟输出齐全的环境中执行项目原有导出入口。

```shell
conda run -n env-py313 python -m src.web.build --config config/web.toml
```

导出完成后重启网页服务。服务启动时检测输入文件校验值，准备对应版本的首页缓存。生成文件和构建产物保留在 outputs 中，提交 PR 时包含网页源代码及测试。

前端开发可运行 `npm run dev`，默认把 `/api` 转发到本机 8765 端口。另一个本地端口可通过 `GRIDX_API_URL` 设置。

## 本地数据边界

网页的业务数据来自项目现有网页导出契约，页面通过本机 `/api/` 读取。

| 本地导出文件 | 内容 |
| --- | --- |
| `manifest.json.gz` | 年份、数据层、图层和资源类别清单 |
| `boundary/common.json.gz` | 行政边界、地区稳定 ID、名称和级别 |
| `layers/{stage}/{layer}.json.gz` | summary 与 GeoJSON 要素 |
| `layers/{stage}/resource/{class}.json.gz` | 指定资源类别的本地分布 |
| `application/uc.json.gz` | 原始时间、功率系列、类别与模拟摘要 |

case 资源沿用现有 mapping 文件别名。负荷地图的年度用电量须在 summary 中声明 TWh，设备功率使用 MW，网络电压使用 kV。

`ui_data.py` 只读取这些导出文件，使用 Python 标准库生成首页和省级指标。省名来自导出的边界。它不导入 standard、mapping、case 或 app 模块，也不解析这些模块的源码。后端内部模块的调整只需继续满足此网页导出契约。导出字段或单位发生变化时，应在 web 层更新适配与契约测试。

缓存以输入文件和适配代码的校验值分版本保存到 `outputs/web-ui-data/`，使用临时文件原子写入。损坏缓存可重建，导出文件缺失或格式不兼容时页面给出提示。

地图的地理背景使用上述本地行政边界。新版 MapLibre 和兼容 Leaflet 界面均不请求在线瓦片、远程样式或字体。图表依赖随构建打包。HTML 的 `connect-src 'self'` 限制页面数据请求到同源。原始数据的下载和处理由项目其他模块负责，前端不会额外抓取业务数据。保留的旧版界面沿用其原有实现。

## 指标与结果说明

- 全国与省级容量基于导出中有坐标的标准层发电记录。无法唯一归属的记录单独统计。它与官方在运装机统计的口径不同。
- 省级用电量与名称来自本地负荷和边界文件。缺少年度最大负荷时保持空值。
- 特高压按交流至少 1000 kV、直流至少 800 kV 的支路 ID 去重。跨省支路可能计入多个省。
- 模拟时段、时点数量、求解模式和系列取自本地结果，兼容不同长度的逐小时结果。区域受电系列显示为「区域受电」。
- 新闻消息默认空列表，可在前端维护经确认的内容。

## 验证

```shell
conda run -n env-py313 python -m unittest src.web.test_ui_data -v
cd src/web/frontend
npm test
npm run build
cd ../../..
conda run -n env-py313 python -m src.web.check
```

最后一项要求网页服务已经启动。它逐项比较 HTTP 返回值与本地导出文件，并核对首页缓存的来源校验值。

小型测试使用明确构造的样例，覆盖后端模块不可导入、不同时间长度、系列显隐导出、混合几何、省界洞、未归属记录、重复支路、缓存更新和损坏缓存重建。完整数据加载还应使用本地真实数据包在浏览器验证。
