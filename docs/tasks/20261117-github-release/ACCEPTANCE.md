# 目标契约

| 标准 | 验证方式 | 结果 |
| --- | --- | --- |
| 名称、描述、Topics 与实际功能一致 | 对照源码和 README | 通过 |
| 克隆后可按文档安装与启动 | 隔离虚拟环境安装元数据检查、合成服务 HTTP/页面 | 通过；完整新环境依赖下载未执行 |
| 本地视频、数据库、密钥不进入 Git | `git check-ignore`、待发布文件清单 | 通过 |
| 截图来自合成数据且页面可读 | Edge 截图、目视检查 | 通过 |
| 许可证范围清楚 | 仓库原有 Apache-2.0 文本及 README 声明 | 通过；未替换原许可证 |

测试数据集：`datasets/README.md`。停止条件：所有标准均有证据，审核结论为通过或明确风险接受。

最终状态：已达成。发布资料已同步至用户克隆仓库，提交 `7a70013` 已推送至 `origin/main`；远端哈希、README、截图、Apache-2.0 许可证、About 描述及 12 个 Topics 已核对。截图：`docs/screenshots/study-detail.png`、`docs/screenshots/download-import.png`。

验证结果：隔离虚拟环境 `pip install --dry-run --no-deps --no-build-isolation -e .` 成功；`python3.12 -m compileall -q video_study`、`node --check video_study/player.js` 通过；合成服务 HTTP 200，Edge 控制台无错误；`git check-ignore` 覆盖配置、视频、数据库、原有真实截图和构建产物。两轮审核完成，无当前需修复项。

第二阶段验证：克隆仓库用配置样例启动 HTTP 200；`git diff --cached --check` 通过；`git ls-remote origin refs/heads/main` 与提交 `7a70013db869561e54c7319b7c7f412867acc89f` 一致；GitHub 页面与刷新后的 API 确认 About 元数据。三轮执行/审核，计划、代码和验收审核均为通过。
