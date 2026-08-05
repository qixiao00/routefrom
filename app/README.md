# RouteFrom 前端

此目录是 RouteFrom 的 Web 前端工作区，使用 Next.js、React 和 TypeScript。

当前阶段只包含时空工作台的界面骨架：

- 顶部命令栏和时间范围入口；
- 左侧数据集、图层和时间切片区域；
- 中央 MapLibre 地图画布；
- 右侧所选对象检查器；
- 底部可展开的时间轴。

界面中的轨迹和统计均为演示数据，尚未连接 Neon 数据库或真实足迹处理结果。

## 本地运行

```powershell
corepack pnpm install
corepack pnpm dev
```

默认访问地址为 `http://localhost:3000`。
