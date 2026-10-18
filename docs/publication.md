# 公开发布说明

本仓库是课程项目的公开初步验证版，不是原有私人仓库的镜像。公开内容包括当前 Python 构建器源码、已保存的课程示例智能体、示例程序、自动测试、项目文档、精选图像，以及用户录制的最终演示视频和可编辑字幕。`course-video/history/` 保留较早合成配音演示作为开发过程材料。

不公开：本机 `.venv`、`artifacts/` 中的临时上传项目及运行记录、账号凭据、API Key、请假条、聊天截图 Word、原始“上/下”录屏，以及留作独立验收的盲测压缩包。这些材料留在用户本机，避免把个人资料和临时数据一并公开。现有私人仓库保持原状。

GitHub 仓库名须使用 ASCII 字母、数字及 `.`, `-`, `_`，因此 URL 使用 `personal-agent-builder-initial-validation`；项目展示标题保留“Personal Agent Builder 初步验证”。

## 发布前核查

- 只复制原项目 Git 追踪或未忽略的 130 个源码/文档/示例/测试文件；未复制原项目 Git 历史。
- 另外收录最终录屏及字幕、较早的课程演示及字幕。
- 用自动规则扫描准备提交的文本文件中的密钥和凭据样式，并复核待提交文件清单与大文件。
- 运行发布副本自动测试，核对 GitHub 仓库可见性、默认分支和远端文件。实际结果见 [变更记录](changes.md)。

## 远端核验结果

2026-10-19 已推送至 [guanluhao/personal-agent-builder-initial-validation](https://github.com/guanluhao/personal-agent-builder-initial-validation)。GitHub 返回可见性 `public`、默认分支 `main`；远端提交 `fc55a8fb055ab25054099600da7da1753f0b16a4` 与本地一致。远端 API 已读取 README、测试文件、网页入口、最终演示视频（24,196,756 字节）及字幕。发布副本自动测试 **44 passed in 73.50s**，136 个待提交文件的文本凭据样式扫描没有发现密钥。
