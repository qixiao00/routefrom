# RouteFrom 前端

RouteFrom 是一个以地图为中心的个人足迹工作台，使用 Next.js 16、React 19、TypeScript、MapLibre GL、Zustand 和 Motion。

当前网站已接入账号的 PostgreSQL/PostGIS 已发布处理结果，支持：

- 在 MapLibre 地图上显示确认轨迹、静止事件、未知缺口和常去地点；
- 创建、删除和编辑任意多个不连续时间区间，区间之间绝不连线；
- 地图、所选统计和证据检查器共享同一选择；
- `/library` 多份 CSV 上传、冲突选择、撤销/恢复和后台任务进度；
- 账号云端保存的时间区间、图层开关、地球相机及面板开合，另存/恢复命名视图；
- 账号隔离的本地地点备注（备注的服务端保存仍待完成）。

## 配置个人足迹库

复制 `app/.env.example` 为 `app/.env.local`，填入数据库连接、认证密钥和站点地址；根目录 `.env.local` 为后台与迁移提供对应配置。真实配置不能提交到 Git。网页使用池化连接，迁移和后台使用直连。两个服务必须共享私有持久目录 `FOOTPRINT_DATA_DIR`。

先在仓库根目录安装处理器，执行迁移，再启动后台（环境变量需通过你的启动器加载，不要把连接串写进命令）：

```powershell
python -m pip install -e .\processor
python -m routefrom_pipeline.database.migrate
python -m routefrom_pipeline.library_worker --data-dir "C:\private\routefrom-data"
```

网站主页要求登录，注册后默认空库；不会显示本机旧 JSON 或其他账号的数据。到 `/library` 上传原始 CSV，后台处理成功后原子发布地图。待决冲突或失败不会替换上一版。

顶部云朵按钮管理地图视图。最近视图在操作后自动保存，命名视图保留独立快照；配置绑定个人库，不依赖某次生成的数据集。保存的是当前完整 MVP 地图状态：三维地球/当前底图/无地形与建筑、经纬度/缩放/倾斜/方位、六类图层开关、任意不连续时间区间和左右面板开合。不包含派生几何、选中事件或地点备注，也不声称支持尚未实现的图层样式/排序/面板尺寸。

视图写入需要当前页面身份与服务端会话一致；另一个窗口换账号时拒绝旧页面的请求。最近视图带版本号，两个设备同时修改会提示冲突，先读取账号视图，再选择恢复或保存本地视图。断网改动仍保留本机，刷新不会静默丢弃；页面退出前会尝试保存，但关机/断网不保证最后请求成功。最多 100 个命名视图，名称最多 80 字符，配置请求限制 32 KiB/64 个时间区间。

启动接口只发送当前账号的元数据和地点，不下载所有轨迹；事件按所选时间请求，轨迹按视野、时间区间和缩放精度请求。邻近地图区域可复用缓存，切换账号会清空缓存；选定时间的完整距离与聚焦范围不随地图平移改变。旧预览导出命令仍可用于离线算法审计，不再作为网站数据源。

## 本地运行

```powershell
corepack pnpm install
corepack pnpm dev
```

访问配置的站点地址，默认 `http://127.0.0.1:3000`；它须与 `BETTER_AUTH_URL` 一致。验证命令（在 `app/` 中）：

```powershell
corepack pnpm test
corepack pnpm test:worker
corepack pnpm typecheck
corepack pnpm build
corepack pnpm bench:viewport
corepack pnpm audit:geometry
```

MapLibre 6 的 GeoJSON 处理依赖独立 Worker。`dev` 和 `build` 会先从已安装的包复制 Worker、共享模块和许可证到 `public/maplibre/<版本>/`，地图运行时显式配置该地址。不要绕过准备脚本直接运行 `next dev`，也不要仅复制 Worker 而漏掉其共享模块。生成资源不入 Git；`test:worker` 校验版本一致性并实际启动发布的 Worker、建立轨迹索引，防止“有底图和统计但无轨迹”的回归。

轨迹块使用 PostGIS 空间与时间索引筛选，再按几何重要度和缩放精度控制返回量；时间边界与语义锚点不能为点数预算被删除。普通移动、相距至少 500 m 的稀疏连接、航空／高速候选分层；静止事件、高速候选默认隐藏。重复经过的路线聚合加粗，附近地点在显示层合并。MapLibre/WebGL 保留三维地球、平面街道且无立体建筑。deck.gl、热力图及百万点性能仍未完成。

地图不会把缺口两端直接连起来。只有处理器判为可展示且完整落入某个选定区间的局部路线推测才出现在虚线层，不计入确认距离。

短时定位漂移、速度矛盾造成的连续性问题与长时间未采样分别解释；检查器不会把前者笼统说成手机没带或关机。

真实 HTTP/worker 验收仅写入 `routefrom_integration*` 独立测试库的临时账号与文件，退出时清理。先构建，启用测试后运行：

```powershell
$env:ROUTEFROM_RUN_DATABASE_TESTS='true'
$env:ROUTEFROM_HTTP_VERBOSE='true'
node --env-file=.env.local --test scripts/library-http-integration.test.mjs
```

设置 `ROUTEFROM_RUN_BROWSER_TESTS=true` 可额外使用 Playwright + 已安装的 Microsoft Edge 在隔离浏览器中验收地图、云端视图、跨设备恢复、并发冲突、离线改动和换账号，截图留在忽略目录 `artifacts/`。端口 3100 必须空闲。地点备注的服务端保存、CSV 导出、完整备份恢复、全量性能和公网部署仍待完成，进度见 `docs/WEB_DATA_LIBRARY.md`。

只验收浏览器时可设置 `ROUTEFROM_BROWSER_SMOKE_ONLY=true`，使用两份短合成数据；它不执行完整导入生命周期，不能代替上面的全流程验收。
