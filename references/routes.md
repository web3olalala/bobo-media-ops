# 2026-09-27 核对的 TikHub 路由

以下是公开文档核对，不等于本账户的真实字段已联调。`provider.py` 使用这些 REST 路由；MCP `tools/list` 只用于当前能力发现，不能因为列出某工具就假定完整字段。

| 平台 | 操作 | 文档 |
| --- | --- | --- |
| 抖音 | 搜索视频 V2 | [官方端点](https://docs.tikhub.io/370212780e0) |
| 抖音 | 视频榜 | [官方端点](https://docs.tikhub.io/252393853e0) |
| 抖音 | 账号近期作品 | [官方端点](https://docs.tikhub.io/186826223e0) |
| 抖音 | 视频评论 | [官方端点](https://docs.tikhub.io/186826225e0) |
| 小红书 | 搜索视频笔记 | [官方端点](https://docs.tikhub.io/420136398e0) |
| 小红书 | 账号笔记 | [官方端点](https://docs.tikhub.io/420136396e0) |
| 小红书 | 笔记评论 | [官方端点](https://docs.tikhub.io/420136394e0) |
| 小红书 | 视频笔记详情 | [官方端点](https://docs.tikhub.io/420136392e0) |

[TikHub MCP 官方说明](https://mcp.tikhub.io/) 采用初始化、会话 ID、`tools/list`、`tools/call` 流程。费用只按当次具体端点价格和账户信息确认，不能拿通用起价当硬预算。
