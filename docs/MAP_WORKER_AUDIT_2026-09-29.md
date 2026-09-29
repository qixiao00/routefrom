# 地图有统计但无轨迹：Worker 审计

## 证据与根因

用户截图显示厦门视野、183 条视野内路径、558.7 km 确认距离，但轨迹、静止事件和地点均未绘制。浏览器诊断进一步确认：

- 183 条路径均有至少两个坐标；开启的普通和稀疏图层合成 2 个 MultiLineString、179 个独立线段。
- `observed-tracks` 数据源和 `track-lines` 图层存在，源序列化包含两个要素。
- `querySourceFeatures` 和 `queryRenderedFeatures` 都返回零；显式 `setData()` 也没有完成。
- 已安装 MapLibre 6.1.0 的默认 Worker 地址依赖 `import.meta.url`。当前 Turbopack 产物把该表达式指向 `maplibre-gl-dev.*.mjs` 主模块，并未正确启动 Worker。主模块不能响应 GeoJSON Worker 消息，因而数据源停在处理阶段。

这比此前对视野事件、旧视图状态的推测更直接：轨迹查询已经成功，失败发生在地图 Worker 初始化。底图栅格可显示，不能证明 GeoJSON 绘制链路正常。

MapLibre 官方说明了 v6 打包环境必须配置 Worker 地址，以及 Turbopack 需要保持 Worker 和共享模块相邻：
[MapLibre 安装说明](https://maplibre.org/maplibre-gl-js/docs/)。

## 修复

- `prepare-maplibre.mjs` 从实际安装包准备同版本 Worker、共享模块和许可证；开发启动与生产构建显式执行它，不依赖包管理器是否运行生命周期钩子。
- `maplibre-runtime.ts` 在创建任何地图前设置版本化的本地 Worker URL，并将配置过的同一模块交给 React 地图组件。
- 取消地图实例复用，避免开发热更新复用此前初始化失败的 Worker/地图实例。
- 删除渲染期间直接调用 `setData` 的临时诊断。

## 验证范围

- 发布资源与安装包逐字节一致。
- 在独立线程实际导入发布的 Worker 及其相邻共享模块，确认其注册消息处理器、完成 GeoJSON 加载并生成非空瓦片索引。
- 生产构建成功；23 项前端测试通过。
- 全局、局部、厦门多缩放级别的几何偏离审计通过，未发现超容差边；这不等价于全部 GPS 异常的准确率验证。
- 当前浏览器完成刷新后的绘制数量和异常线视觉验收仍需运行时证据；不能仅凭这些离线检查宣称原目标全部完成。
