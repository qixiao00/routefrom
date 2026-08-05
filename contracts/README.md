# RouteFrom 共享契约

`workspace.schema.json` 是前端、查询 API 和 Python processor 共同遵守的传输边界。

约定：

- 时间一律使用带时区的 ISO 8601 instant；区间为左闭右开 `[start, end)`。
- 多个时间区间必须在服务端规范化为排序、去重、合并后的并集。
- 所有派生实体同时携带 `logicalId` 和 `processingRunId`。
- 确认轨迹与推测连接分开传输；`isInferred` 不得被客户端忽略。
- JSON Schema 负责低体量控制数据。大量轨迹顶点在保持相同字段语义的前提下使用 Arrow/二进制传输。
