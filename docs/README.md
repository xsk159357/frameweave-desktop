# FrameWeave 文档中心

> 项目文档统一落点（2026-10-14 文档分层建立）。**新文档一律落本目录分层，禁止写入仓库根。**
> 仓库根 `README.md` 只保留一句身份与入口；完整内容见下。

## 分类约定

| 子目录 | 用途 | 示例 |
|---|---|---|
| `guides/` | 指南 / 运维 / 约定 | 开发指南、发布运维手册、提交约定、云端授权与发布规范 |
| `specs/` | 技术规范（契约权威源） | plugin-development-spec（v4 现行）、node-plugin-spec（v1 历史参考） |
| `plans/` | 设计 / 方案 / 任务拆解 | ui-redesign-plan、ui-frameless-plan、settings-plan、M1_任务拆解 |
| `records/` | 完成记录 / 变更记录（流水账） | M1~M19 完成报告、v0.2.3_变更记录、全量修复记录 |
| `reports/` | 验证 / 验收 / 深度报告 | cloud_e2e_report、授权服务器连通性验证报告、FrameWeave_项目深度分析报告 |

## 索引

### guides/ — 指南与约定
- [开发指南](guides/开发指南.md) — 快速开始 / 测试 / 构建发布（README「快速开始」指向此）
- [发布运维手册](guides/发布运维手册.md)
- [会话交接与环境清单](guides/会话交接与环境清单.md)
- [提交约定](guides/提交约定.md)
- [云端授权与发布规范](guides/云端授权与发布规范.md)
- [插件商城自动安装说明](guides/插件商城自动安装说明.md)
- [服务器自动更新说明](guides/服务器自动更新说明.md)
- [节点市场化_空壳分发说明](guides/节点市场化_空壳分发说明.md)
- [待办清单](guides/待办清单.md)

### specs/ — 技术规范
- [插件开发规范 v4（现行权威）](specs/plugin-development-spec.md)
- [节点插件规范 v1（历史参考）](specs/node-plugin-spec.md)

### plans/ — 方案与拆解
- [UI 重设计方案](plans/ui-redesign-plan.md)
- [UI 无边框方案](plans/ui-frameless-plan.md)
- [设置页方案](plans/settings-plan.md)
- [M1 任务拆解](plans/M1_任务拆解.md)

### records/ — 完成记录
- M1 ～ M15 完成报告、[平台化 M16-M19 完成报告](records/平台化_M16_M19_完成报告.md)
- [v0.2.3 变更记录](records/v0.2.3_变更记录.md)、[全量修复记录](records/全量修复记录.md)
- [云端签名部署修复记录 20261011](records/云端签名部署修复记录_20261011.md)、[s14 商城自动刷新](records/s14-market-autorefresh.md)

### reports/ — 验证与深度报告
- [FrameWeave 项目深度分析报告](reports/FrameWeave_项目深度分析报告.md)
- [cloud E2E 报告](reports/cloud_e2e_report.md)
- [授权服务器连通性验证报告 20261009](reports/授权服务器连通性验证报告_20261009.md)
- [服务器安全运维审计 20261017](reports/服务器安全运维审计_20261017.md)
- [部署前检查清单与回滚步骤 20261017](reports/部署前检查清单与回滚步骤_20261017.md)

## 维护约定

- 移动 / 新增文档后更新本索引与仓库根 `README.md` 入口。
- 文档引用一律相对仓库根的 `docs/...` 路径，不得使用绝对机器路径。
- 被引用的文档移动前必须 grep 全仓相对链接并同步修正（参照工作区 `docs/reports/README.md` 链接修正记录）。