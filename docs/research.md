# 技术调研与路线选择（2026-10-15）

本页区分官方规范、项目源码和本项目已验证的事实。外部资料用于设计依据，是否可用仍以本地测试记录为准。

| 方向 | 已核实的成熟方案 | 可复用处 | 本项目决定 |
|---|---|---|---|
| Paper2Agent | [官方仓库](https://github.com/jmiao24/Paper2Agent)采用“研究资料和代码→Paper2MCP→专属 Agent”流程，并要求工具验证及独立使用说明；[论文](https://arxiv.org/abs/2509.06917)描述 Tools、Resources、Prompts 的协作结构。 | 能力筛选、源码绑定、隔离环境、验证与证据等级。 | 复用方法，不照搬面向论文及 Claude Code 多 Agent 编排的实现；我们的输入是任意 Python 项目，先给用户能力说明。 |
| MCP | [官方规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)定义 Tools 的发现、输入 Schema 和调用；[Server 概览](https://modelcontextprotocol.io/specification/draft/server/index)区分 Tools、Resources、Prompts。官方 [Python SDK v2](https://py.sdk.modelcontextprotocol.io/get-started/first-steps/)使用 `MCPServer` 与装饰器。 | 用官方 `mcp` 包生成每个已批准能力的工具，并附能力清单 Resource 与使用 Prompt。 | 不自行实现 MCP 线协议；本地 stdio 优先，不部署公网。实际曾遇到 v1 `FastMCP` 导入失败，已改用 v2 并通过官方客户端测试。 |
| Codex | [官方 Python SDK 文档](https://learn.chatgpt.com/docs/codex-sdk)说明 `openai-codex` 可控制本地 app-server；[CLI 文档](https://learn.chatgpt.com/docs/cli/reference)提供 `codex exec`、只读沙箱和结构化输出。 | Codex 负责把自由表达映射为结构化工具请求，运行时再做白名单和路径校验。 | 第一版选已有登录的官方 CLI，开发成本更低；Python SDK 留作持久会话扩展。2026-10-15 本机 `codex login status` 返回 ChatGPT 登录，结构化路由真实调用通过。 |
| 订阅认证 | [OpenAI 登录指南](https://developers.openai.com/siwc/quickstart)说明 ChatGPT 计划使用需要专门授权的范围；[app-server 指南](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)给出受支持的 OAuth 方式。 | 官方登录，不接触或复制凭据。 | 本项目调用本机已登录 Codex CLI。没有把 ChatGPT 订阅当作普通 API 额度，也不读取 `auth.json` 或提取 OAuth Token。独立分发后，使用者须自行按官方流程登录。 |
| Pi AI | Pi 的[提供商说明](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/providers.md)描述交互式 `/login` 选择 OpenAI Codex；其凭据由 Pi 自身管理。 | 可作未来可替换对话前端。 | 不把 Pi 凭据搬入本项目；第一版不增加第二套运行时。 |
| Python 分析与修改 | [Python `ast`](https://docs.python.org/3/library/ast.html)适合只读结构发现；[LibCST](https://libcst.readthedocs.io/en/latest/)适合保留格式的源代码修改；[pytest](https://docs.pytest.org/)用于回归测试。 | `ast`、隔离副本、pytest 与差异审查。 | 第一版静态分析用标准库 `ast`；升级在副本中应用明确变更，运行回归后等待用户启用。复杂自动代码修改可引入 LibCST。 |

## 当前路线及限制

1. **导入和说明**：只读扫描源码、入口与依赖，生成 JSON 和中文说明；不执行安装脚本。静态发现一律标为“线索”。
2. **确认与生成**：用户审阅工具清单；确认文件绑定清单 SHA-256。生成独立 Agent 源码和项目文件副本，拒绝覆盖已有目录。
3. **运行**：每条对话经官方 Codex CLI 生成结构化选择；执行前仍由本地白名单校验工具名、参数、路径和超时。模型会收到批准的工具目录、必要的会话记录以及本次小文件预览；需要解释项目文件时可在受限清单中选择只读查看，项目内容始终视为不可信数据。
4. **升级**：在副本上生成差异并验证；用户确认精确版本摘要后才可启用，保留旧版供回退。

当前的静态分析**不能自动推导任意 Python 项目的完整安全工具接口**，首次能力清单仍需要核对入口、参数和副作用。该限制是后续路线图重点，不应描述为已解决。

## 2026-10-18：参考现成智能体构建方法后的修订

[OpenAI 的技能说明](https://developers.openai.com/plugins/concepts/skills)将可复用的工作流程指导与实际执行工具分开；[技能构建指南](https://developers.openai.com/plugins/build/skills)建议用真实任务、信息不完整和边界情况检查流程；[技能评测文章](https://developers.openai.com/blog/eval-skills)强调按可观察结果验收。这里借鉴的是**设计与验证方法**，并没有声称将某个外部技能安装到了用户的电脑或让它自动获得程序权限。

据此，生成器现在分开保存两类信息：原程序分析得出的**已批准工具清单**，以及用户回答澄清问题后确认的**使用方式**。使用方式进入生成智能体的对话提示、独立说明和 MCP Prompt；它不会增加可执行工具或扩大路径权限。创建前最多提出三个与当前程序和用户目标有关的问题。创建时的测试数据不再成为以后调用的默认输入或列名。生成后可在同一智能体中反复添加不同数据文件；信息不足时先问，并在下一轮保留这次添加的文件。自动测试覆盖改名字段、换文件、追问后继续使用；真实模型调用也检查了用户的 `test.csv` 与另一份不同列名的文件。

[OpenAI Agents 概览](https://developers.openai.com/api/docs/guides/agents)提供更完整的托管式智能体方案，但本项目目前使用本机已登录的官方 Codex 命令行处理自由语言，不切换到需要另行配置 API 账单与凭据的路线。MCP 仍用官方 Python SDK 提供受控工具；这两部分各司其职。
