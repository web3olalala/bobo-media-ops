# 模式与原创口播

读取同任务 `analysis_input.json`、可信逐字稿、普通作品对照、用户级 `profile.json` 与事实库。`analysis/patterns.json` 是模式数组：每项含 `name`、`support_post_ids`、`support_urls`、`excerpt_positions`、`count`、`sample_size`、`normal_counterexamples`、`confidence`、`conditions`。去重后不足三条独立作品不得叫“反复跑通”；同素材跨平台转载只算一条。模式仅是抽象结构，不是他人全文换词模板。

先确定 Bobo 的目标受众问题、差异化角度、论点和真实可核实例。写 2–3 个开头备选、结构说明、来源与事实核查于 `analysis/script-check.md`。完整正文单独写 `analysis/script.txt`：一整段自然中文，无标题、引用标记、分点和占位。没有个人经历可用中性实操写法，不能编造 Bobo 或原作者的经历与成绩。若必须补事实，正文标为待补稿，不能称可发布。

核对产品功能、安装、价格、限额、效果时优先官方资料或用户亲测；区分“设计支持”“代码实现”“真实跑通”。自检连续独特表达、只换同义词、挪用经历、夸大数据与时长。`validate --scene script` 只能检查文件和格式，原创与事实仍需 Agent 实际审阅。
