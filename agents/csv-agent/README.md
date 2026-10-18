# csv-personal-agent

这是经用户确认范围后生成的独立 Agent。项目副本在 `project/`，调用记录保存在 `records/`。

## 启动

安装本项目 README 所列 Python 依赖后，在此目录运行 `python agent.py` 进入终端对话，或运行 `python agent.py <需求>` 执行一次请求。`python mcp_server.py` 启动 MCP stdio 服务。

每条对话都会由本机已登录的官方语言模型分析；无需 API Key。当前智能体只执行已确认的程序功能，不会自行安装项目依赖。

## 已注册能力

- `summarize_csv`：汇总项目副本中 CSV 文件的数值列，输出 JSON。

新需求会写入 `records/upgrade_requests.jsonl`，经 Builder 隔离修改、测试和用户确认后才能启用。
