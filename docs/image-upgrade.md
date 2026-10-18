# 图片标注升级候选（尚未启用）

用户决定本轮只以真实图片确认，不需要 PDF。为演示轻量升级流程，测试需求改为：“在模型图片上标注密度异常点的网格坐标。”

## 需求与差异

- 正式二维 Agent 收到这句话，返回 `upgrade_requested` 并记录请求；没有直接执行未经批准的新功能。
- 原 `seismic/plotting.py` 的模型图只用颜色显示密度异常，没有 `x/z` 坐标文字。候选补丁仅在绘制 `rho` 切片时检测与背景密度不同的单元，并用 `ax.annotate` 写出网格索引。
- 差异只涉及隔离版本中的 `seismic/plotting.py`，原科研仓库和正式 Agent 的当前版本均未修改。复现脚本是 `python -m scripts.stage_image_annotation` 与 `python -m scripts.stage_formal_image_upgrade`；脚本不会启用候选。

## 实际验证

- 隔离候选的测试脚本调用真实 V1 建模与绘图，检查图中存在 `x=30, z=24` 文本，并检查 4 张 PNG 文件头和 4 张 SVG；执行结果：`test_exit_code=0`。
- 对正式 Agent 副本暂存相同补丁，实际生成 4 张 PNG 和 4 张 SVG；补丁后的 `plotting.py` 与前一候选 SHA-256 完全一致。
- 再以已移动到 `(x=35,z=20)` 的配置运行候选，生成新图片并人工检查，图片上可见 `x=35, z=20`。[查看图片](media/annotation_candidate.png)。
- 正式候选版本摘要：`04db160f47d423358c86c5d0ef185d57e2c931eeca7f28656744cc6d31a0b131`。本地差异与运行记录保存在忽略提交的 `artifacts/formal_image_annotation_stage/`。

## 当前状态

`activated: false`；正式 Agent 仍使用原始绘图代码。此候选只负责图像标注，不增加 PDF、不运行正演或 RTM。用户确认该精确版本后，Builder 才可切换版本指针；保留原版以供回退。该示例是预先编写的低风险升级配方，**不是**对任意自然语言需求的通用自动编程器。
