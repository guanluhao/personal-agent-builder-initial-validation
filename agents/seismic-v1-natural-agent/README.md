# seismic-v1-natural-agent

这是经用户确认范围后生成的独立 Agent。项目副本在 `project/`，调用记录保存在 `records/`。

## 启动

安装本项目 README 所列 Python 依赖后，在此目录运行 `python agent.py` 进入终端对话，或运行 `python agent.py <需求>` 执行一次请求。`python mcp_server.py` 启动 MCP stdio 服务。

每条对话都会由本机已登录的官方语言模型分析；无需 API Key。当前智能体只执行已确认的程序功能，不会自行安装项目依赖。

## 已注册能力

- `preview_model`：调用二维 V1 的真实建模与绘图函数，预览当前配置，生成模型和观测系统 PNG/SVG 图片。
- `shift_anomaly_meters`：把当前二维单网格密度异常按物理距离移动。left_m 是向左多少米，down_m 是向下多少米；从当前密度异常配置继续修改，原配置不覆盖。
- `extend_tunnel`：把二维隧道示意配置中唯一的目标空腔向右延长 meters 米；只改右端物理边界，保留旧配置。不是完整隧道施工或波场计算。

新需求会写入 `records/upgrade_requests.jsonl`，经 Builder 隔离修改、测试和用户确认后才能启用。
