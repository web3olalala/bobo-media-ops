# 需求映射与验证记录（2026-09-27）

采用本地默认分支：shengjiang-research `9a17a4d9f3855b9e4db9e1f9921667283b8dbdf8`；konglong-research `a48d52288ec008eff9084cbf88aac2c2b5eecf10`。两个上游均未修改。生姜的 REST/媒体/火山 AUC 代码保留在 `vendor/shengjiang/`；恐龙哥的 MCP 动态发现代码保留在 `vendor/konglong/`。新代码修正了原相对分数包含目标作品、缺失互动当零的口径，并将 Key 存储移出公开 Skill 包。版权与复用路径见 `THIRD_PARTY_NOTICES.md`。

| 需求 | 实现与产物 | 验证状态 |
| --- | --- | --- |
| 单 Skill、配置、分享包 | `SKILL.md`、`setup/doctor/package`，配置写用户目录，ZIP allowlist | 已实现且已验证：ZIP 检查无私人文件；WorkBuddy 发现机制尚未实测 |
| 今日主题/热榜/覆盖 | `plan search` 固定时区边界；抖音搜索与视频榜分源，小红书搜索，无榜时标缺失；分页/去重/截断状态 | 已实现但真实服务未验证；日期与重复游标离线测试通过 |
| 低粉高播放与缺失语义 | `core.py` 字段解析、阈值、互动候选、账号先前作品基线、低样本置信 | 已实现且已验证：离线单测；真实播放字段仍待小样本核对 |
| 账号、评论、视频/字幕/ASR | `provider.py` 账号/评论 REST；`media.py` 复用上游视频解析，安全下载，官方字幕优先，ASR 任务 ID 续查 | 已实现但真实服务未验证；视频号依赖缺失导致降级；所有视频批量转写尚未宿主验收 |
| 预算与安全 | `plan` 离线、`Ledger` 请求/重试拦截、未知价格停下或按明确请求数模式、内网媒体地址拦截、CSV 公式转义 | 已实现且已验证：离线单测；实际账单未验证 |
| 对标分析、选题、模式和原创口播 | 4 份 references 定义 Agent 输入/输出；`validate/export` 检查证据 URL、模式独立作品数、一段口播；Agent 在合成样本上实际写报告与稿件 | 已实现且已验证：合成样本流程；WorkBuddy 自然语言三场景仍未验证 |
| XLSX/CSV/JSONL | `write_tables/export`；长文本另存媒体目录 | 已实现且已验证：13 项离线测试中检查 XLSX 可打开、CSV/JSONL 条数和中文 |
| 抖音和小红书真实小样本 | 需 TikHub Key、当前端点价格、明确费用授权 | 未完成：本次未运行付费接口，不声称真实爆款结论 |
| WorkBuddy 宿主发现/安装 | 官方说明已核对；本机未在 WorkBuddy 安装 | 未完成：需要在该宿主中测试 Skill 发现与三条自然语言入口 |

离线命令：`python3 -m unittest discover -s tests -v`。结果：13 tests passed。另用 `tests` 外的合成 4 作品、4 段简短合成逐字稿，执行 `import → Agent 写 topics/account-report/patterns/script → validate → export`，三个验证入口均返回 `complete`；该结果只证明离线流程和证据约束，不代表真实平台或宿主已跑通。

保留的缺口：MCP 当前用于动态发现，采集使用固定且文档核对的 REST 路由；未做 MCP 收集回退。账号视频短链到作者的真实响应字段需小样本确认。小红书播放量可能缺失，不能承诺真实低粉高播放识别。ASR 遇提交结果不确定时停住人工核对，不自动重交。当前 `media` 每次只查询一次既有 ASR 任务，宿主可按预算续跑。WorkBuddy 本地 ZIP 导入路径和版本尚不能宣称已验证。
