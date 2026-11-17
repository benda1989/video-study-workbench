# 目标契约

| 标准 | 验证方式 | 结果 |
| --- | --- | --- |
| 名称、描述、Topics 与实际功能一致 | 对照源码和 README | 通过 |
| 克隆后可按文档安装与启动 | 隔离虚拟环境安装元数据检查、合成服务 HTTP/页面 | 通过；完整新环境依赖下载未执行 |
| 本地视频、数据库、密钥不进入 Git | `git check-ignore`、待发布文件清单 | 通过 |
| 截图来自合成数据且页面可读 | Edge 截图、目视检查 | 通过 |
| 许可证范围清楚 | 仓库原有 Apache-2.0 文本及 README 声明 | 通过；未替换原许可证 |

测试数据集：`datasets/README.md`。停止条件：所有标准均有证据，审核结论为通过或明确风险接受。

第一阶段状态：发布资料已备齐。第二阶段目标：同步到用户已克隆仓库，验证后提交并推送 `origin/main`；最终结果待填。截图：`docs/screenshots/study-detail.png`、`docs/screenshots/download-import.png`。

验证结果：隔离虚拟环境 `pip install --dry-run --no-deps --no-build-isolation -e .` 成功；`python3.12 -m compileall -q video_study`、`node --check video_study/player.js` 通过；合成服务 HTTP 200，Edge 控制台无错误；`git check-ignore` 覆盖配置、视频、数据库、原有真实截图和构建产物。两轮审核完成，无当前需修复项。
