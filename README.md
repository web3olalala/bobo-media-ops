# BOBO 自媒体运营

一个独立的 Agent Skill，把公开社媒数据整理成可追溯的选题、对标研究和原创口播工作流。面向做 AI 实用教程的创作者，也可以通过编辑个人档案用于其他内容方向。

> 当前状态：离线流程和测试已通过；抖音、小红书真实服务与 WorkBuddy 宿主尚未联调。请勿把示例或代码能力当作已验证的真实爆款结果。详细状态见 [验证记录](VERIFICATION.md)。

## 能做什么

| 场景 | 交付物 | 当前边界 |
| --- | --- | --- |
| 今日主题研究 | 作品证据表、覆盖范围、低粉高播放与互动候选、原创选题 | 抖音搜索与视频榜、小红书搜索有文档核对的 REST 路由；真实返回字段待小样本验证。小红书没有已核对的对应热榜路由。 |
| 视频与对标账号 | 作品、评论、必要媒体与逐字稿、CSV/JSONL/XLSX、带证据的报告 | 抓取范围受预算限制；评论与逐字稿必须注明抽样数，不能把公开评论当作粉丝后台画像。 |
| 模式与口播 | 可复用模式库、2–3 个开头备选、一整段原创口播、独立事实核查 | 主题归纳和写作由宿主 Agent 完成；脚本负责采集、规范化、导出与校验。不会编造创作者经历。 |

只有粉丝数和播放量都可核实，才归入“低粉高播放已验证样本”。缺失值保持为空；只有互动量时列为候选。账号相对表现仅使用目标作品之前的同账号样本，不跨平台直接比较原始播放量。完整规则见 [Skill 入口](SKILL.md)及 [选题口径](references/topic.md)。

## 安装

需要 Python 3.10 或更新版本。将本仓库作为一个完整文件夹放入支持 [Agent Skills 格式](https://agentskills.io/specification)的宿主，或先在本地运行 CLI：

```bash
git clone https://github.com/web3olalala/bobo-media-ops.git
cd bobo-media-ops
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/bobo_media.py setup --timezone Asia/Shanghai
.venv/bin/python scripts/bobo_media.py doctor
```

Windows 将 `.venv/bin/python` 改为 `.venv\Scripts\python.exe`。WorkBuddy 官方说明支持在对话中创建自定义 Skill，并在技能栏查看；**本项目尚未核实 WorkBuddy 当前版本导入现有文件夹或 ZIP 的具体界面操作**，因此不提供未经实测的一键安装指令。[WorkBuddy 官方技能说明](https://www.codebuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Practice-Cases/Create-Skills)

`setup` 在用户配置目录保存配置和可编辑的 `profile.json`，不会把私人档案写进项目。可用 `setup --save-key` 隐藏输入 TikHub Key，或设置 `TIKHUB_API_KEY`。优先级是环境变量、指定或默认密钥文件、macOS Keychain。`doctor` 只检查 Key 是否存在；需显式 `doctor --online` 才做联网环境检查，联网检查也不代表付费端点已联调。

## 先用已有数据试跑（无付费请求）

```bash
python3 scripts/bobo_media.py import --input posts.csv --platform douyin --out my-research
```

支持 CSV、JSON、JSONL、XLSX，自动识别常见中英文列名；遇到歧义会提示，未知数值不会填零。导入表格默认标为“待核源”；只有能确认其平台来源时才使用 `--verified-import`。`analysis_input.json` 是宿主 Agent 后续分析的输入，不是自动生成的研究结论。Agent 写完 `analysis/` 报告后运行 `export --job my-research` 和 `validate --job my-research`。三个可直接发送给宿主的 [自然语言提示词](examples/prompts.md)覆盖选题、对标和口播。

## 真实采集与费用

TikHub API 与火山 AUC 转写是独立的第三方服务，可能收费；安装本 Skill 或配置 Key 都不代表授权扣费。先生成离线计划，检查平台、时区、页数、作品和评论上限、预计请求数与价格缺口：

```bash
python3 scripts/bobo_media.py plan search --query WorkBuddy --platforms douyin,xiaohongshu --hot --out plan.json
```

在明确获得费用与范围授权后，才执行 `run --plan plan.json --out my-research --authorize`。若具体端点价格未知，命令会停止；只有用户明确同意按请求数限制且理解金额上限未核实时，才加 `--authorize-unknown-prices`。已知价格可在用户级配置的 `endpoint_prices_usd` 中按端点填写，并设置 `max_cost_estimate`。转写另需 ASR 凭据、时长与价格配置及单独授权。

`run` 会保存 `manifest.json`、请求账本、原始响应、标准化表格和 `analysis_input.json`。`awaiting_analysis` 表示仍需 Agent 读取真实数据、写出报告或稿件，再运行 `export` 与 `validate`。原始响应和签名媒体地址只留在私人任务目录；不要把整个研究目录公开分享。

## 项目结构与来源

- `SKILL.md`：唯一 Skill 入口；`references/`：三个场景、数据口径与真实路由。
- `scripts/bobo_media.py`：统一 CLI；`core.py`、`provider.py`、`media.py`：离线计算、采集和媒体状态。
- `templates/`：通用档案与输出模板；`examples/`：自然语言提示词；`tests/`：离线断言。
- `vendor/`：保留上游许可的生姜调研媒体模块和恐龙哥 MCP 工具发现模块。复用版本、修改和第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本项目按 MIT 许可证开放。TikHub、火山引擎及两个上游项目均未对本 Skill 作官方背书。当前已运行 13 项离线测试；真实付费接口、账单与 WorkBuddy 三场景仍需后续验收。
