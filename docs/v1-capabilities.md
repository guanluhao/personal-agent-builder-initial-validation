# 二维声学 V1 项目能力说明

来源：私人仓库 `guanluhao/seismic-simulation` 的 `main`，2026-10-15 只读检查；本项目保存的最小源码副本位于 `examples/seismic_v1_snapshot/`。米制移动工具、二维隧道示意配置及延长工具是随后在本项目隔离副本中新加的适配材料，**不属于原仓库已经具备的功能**。此说明不把历史三维流程当作当前二维入口。

1. **总体用途**：为二维声学实验准备速度、密度、震源与接收器模型，生成预览；仓库还包含二维正演、RTM/ISIC 成像代码，但本轮未运行这些高成本功能。
2. **主要功能**：`workflow.py` 提供 `init/build/check/run/image/all` 等入口。当前已实测隔离副本直接调用 `seismic.model.build` 与 `seismic.plotting.plot_inputs`，生成计算输入及模型、观测系统、子波图。
3. **自然语言需求示例**：新生成的[米制示例 Agent](../agents/seismic-v1-natural-agent/README.md)实测听懂“把密度异常向左移动 5 米、向下移动 4 米”“把隧道向右延长十米”，并调用对应的 Python 工具；后续还能要求重新预览。原先“第几格”的旧 Agent 仍保留作历史记录。
4. **输入**：密度异常练习使用 `config.json`；二维隧道示意使用独立的 `tunnel_config.json`。两份配置的网格均为 `[z,y,x]=[41,1,61]`、间距 1 米，含 3 个震源和 51 个接收器。按米移动工具将方向和距离换算成内部网格索引；隧道工具只修改唯一目标空腔的右端物理边界，均另存新配置。
5. **输出**：DDS 模型及几何输入；每次预览真实生成 4 张 PNG 和 4 张 SVG（model、geometry、geometry_grid_nodes、wavelet）。隧道从 10 米延长到 20 米的前后观测系统图见[延长前](media/tunnel_before.png)和[延长后](media/tunnel_after.png)；异常点按米移动的图见[修改后](media/anomaly_meters_after.png)。
6. **环境与依赖**：源仓库 `requirements.txt` 声明 numpy、numba、matplotlib 的固定版本；本轮低成本预览实际使用本机 Python 3.15 环境中的 numpy、matplotlib，未运行需要 numba 的正演。
7. **验证等级**：新 Agent 的两句自然语言均通过官方 Codex CLI 路由，实际调用 Python 并返回 `verified_output: true`；修改后的两种配置均由 V1 建模和绘图函数真实处理。数组检查确认异常点的新位置以及隧道新增的 50 个零速度网格单元。二维正演、RTM、ISIC 和科研物理精度仍**未验证**。PDF 不在交付范围。
8. **风险与成本**：完整正演和成像会增加内存、CPU、磁盘使用；本轮不执行。用户源项目可能包含不可信代码；只在隔离副本和受限能力清单内调用。
9. **适合封装的工具**：旧示例是 `preview_model`、`move_anomaly`；新米制示例是 `preview_model`、`shift_anomaly_meters`、`extend_tunnel`。它们均另存结果并拒绝覆盖已有输出。
10. **建议**：先用预览检查输入，再用米制语言调整；修改后的配置重新预览。删减指定检波点尚未实现和验证，不能当作已有工具展示。

相关真实调用和验证结果见 [变更记录](changes.md)。
