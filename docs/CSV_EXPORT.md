# 个人足迹 CSV 导出

实现日期：2026-10-16。延续既有网站、账号鉴权、私有文件目录和已发布 PostGIS 版本，不引入新的导入器、地图框架或统计口径。

## 用户入口

- 足迹库的每份原始文件提供“下载原始 CSV”。撤销只改变参与计算的状态，仍可下载留存原件。
- 地图顶栏“导出”使用当前任意时间选择，最多 64 个区间。导出不受地图视野、缩放、图层开关和显示预算影响。
- 下载前使用 HEAD 校验身份、文件/版本、来源清单和第一批查询；验证成功交给浏览器原生下载，不把全文件构造成页面 Blob。
- 登录变化、旧版本来源不足、文件缺失、请求失败会提示。下载开始后的网络错误由浏览器下载列表报告，页面不声称已经完整保存。

## 三种数据的边界

| 内容 | 格式与含义 | 不包含的内容 |
| --- | --- | --- |
| 原始文件 | 上传 CSV 原样字节、原始文件名；响应附存档 SHA-256，供下载端核对 | 不做去重、清洗、重新排版或时间裁剪 |
| 合并后的源观测 | 灵敢 14 列及顺序。来自该已发布版本的精确原观测映射，按实际冲突选择去重，含后续被算法判异常的观测。源数值、缺失哨兵和网络文本保留，可重新导入 | 不把异常点悄悄删掉，不把平滑位置冒充源坐标；数值表示可能从 `-1` 变成等值的 `-1.0`，不保证原文字节 |
| 去噪 / 平滑 GPS | 独立的分析 CSV，使用对应版本全精度存储顶点，保留疑似质量和证据，排除质量为 excluded 的点。平滑位置明确不是原始观测 | 不承诺恢复写入数据库前的浮点表示；不含 LOD 简化、插值、推测连线或显示聚合；不是灵敢导入格式 |

时间均采用带偏移的 ISO 输入，并规范为 UTC。区间为 `[start,end)`，重叠和相邻区间先取并集，不固定为月/日，也不按日历重新分桶。选区内没有真实观测时只返回表头，不能导出地图边界插值点。

轨迹列顺序：

`recordedAt,longitudeWgs84,latitudeWgs84,selectionRangeIndex,segmentIndex,segmentPointIndex,sourceObservationId,sourceFingerprint,sourceId,qualityStatus,anomalyProbability,reasonCodes,variant,processingRunId,algorithmVersion`

`selectionRangeIndex` 是排序并合并后的区间索引，`segmentIndex` 是处理运行的真实轨迹段索引；都从 0 开始。消费者必须按这两列共同分组后再连线，不能把不同时间片或观测缺口两端直接连接。`segmentPointIndex` 保留段内原顺序，裁剪后不重新编号。`recordedAt` 为 UTC；原观测 UUID/指纹是当前账号的来源证据，不是跨账号恢复凭证。完整备份另行实现。

## 服务端约束

`GET /api/library/export` 要求会话和发起页面的明确账号身份；原件按所属库、用户、文件三重校验，不能通过其他人的 UUID 下载。时间导出要求明确的已发布运行 UUID，可以导出属于自己的已发布旧版本；处理候选不可导出，撤销排队和后续发布不会悄悄替换当前所选版本。旧处理版本没有来源映射时返回 409，要求重新处理，不按最近坐标猜来源。

数据采用 1000 行 keyset 分页，以字符串保存数据库 bigint 游标，避免 JavaScript 舍入。每批只选导出需要的列，按响应背压输出；不持有整个历史或长期数据库事务。取消下载会中止待发查询，单批 30 秒、整份处理数据下载 5 分钟超时。私有无缓存、附件下载、禁 MIME 嗅探，不泄露私有存储路径。

原始文件校验固定私有 key、实际文件路径和文件大小；私有根目录可挂载，但其下的符号链接不能跳转到其他资产。CSV 引号按标准转义，中文、逗号和换行保留。为了保持原件/源观测真实性，不擅自改写源文本以消除表格公式；界面提示勿在表格软件启用外部 CSV 公式。

## 验证状态

真实短流程接口/worker/Edge 整套通过，包括原生下载、跨两页源数据、64 个不连续区间、完整源精度/中文文本、质量排除与来源保留、取消、文件撤销和账号隔离。具体通过时间、范围及长流程状态记录于 [WEB_DATA_LIBRARY.md](WEB_DATA_LIBRARY.md)，不能将单个阶段等同于整个 PRD 完成。完整备份/恢复、真实全量数据性能和公网部署仍是独立验收项。

快速的真实参数校验可在 `app` 目录、已配置独立测试数据库的情况下运行：

```powershell
$env:ROUTEFROM_RUN_DATABASE_TESTS='true'
$env:ROUTEFROM_EXPORT_VALIDATION_ONLY='true'
node --env-file=.env.local --test scripts/library-http-integration.test.mjs
```

它使用临时账号和生产测试服务器，不导入 CSV。完整流程需将 `ROUTEFROM_EXPORT_VALIDATION_ONLY` 设为 `false`；浏览器由 `ROUTEFROM_RUN_BROWSER_TESTS` 控制，短数据流程由 `ROUTEFROM_BROWSER_SMOKE_ONLY` 控制。`ROUTEFROM_TEST_IPV4=true` 仅用于本机已诊断的 IPv6 路径问题，不修改数据库主机名或 TLS 校验。
