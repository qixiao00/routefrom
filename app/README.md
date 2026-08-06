# RouteFrom 前端

RouteFrom 是一个以地图为中心的个人足迹工作台，使用 Next.js 16、React 19、TypeScript、MapLibre GL、Zustand 和 Motion。

当前 MVP 已接入本地真实处理结果，支持：

- 在 MapLibre 地图上显示确认轨迹、静止事件、未知缺口和常去地点；
- 创建、删除和编辑任意多个不连续时间区间，区间之间绝不连线；
- 地图、所选统计、证据检查器和 Canvas 时间地层共享同一选择；
- 时间游标播放，并在不连续区间之间明确跳转；
- 将时间区间、图层开关和地图相机保存在浏览器本地。

## 准备本地预览数据

预览文件来自处理器输出，不包含在 Git 中。先在仓库根目录安装处理器并导出私有预览：

```powershell
python -m pip install -e .\processor
routefrom-preview "C:\private\linggan.csv" `
  --output ".\data\generated\workspace-preview.json"
```

复制 `.env.example` 为 `.env.local`，开启仅供本机使用的数据端点，并填入预览文件的绝对路径：

```dotenv
ROUTEFROM_ENABLE_LOCAL_DATA_API=true
ROUTEFROM_LOCAL_PREVIEW_PATH=C:/path/to/routefrom/data/generated/workspace-preview.json
```

该端点只读取服务端环境变量配置的 JSON 文件，不把 CSV 路径或数据库连接串发给浏览器。它没有用户认证，因此默认关闭，不应直接部署到公网。

## 本地运行

```powershell
corepack pnpm install
corepack pnpm dev
```

访问 `http://localhost:3000`。验证命令：

```powershell
corepack pnpm test
corepack pnpm typecheck
corepack pnpm build
```

当前 MVP 使用 MapLibre GeoJSON 图层承载约 1.5 万个预览顶点。deck.gl 二进制图层、热力图、聚合图层、百万点性能和完整服务端工作区 API 留到下一阶段。
