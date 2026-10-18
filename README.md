# 拾帧 FrameWeave

无限画布节点化 AI 视频创作软件（当前版本 **0.3.12**；版本真源 = `desktop/package.json`）。

## 结构
- backend/  — FastAPI 本地服务（执行引擎 + 插件运行时 + 授权网关 + 商城）
- web/      — Vite + React 前端（@xyflow/react 画布）
- desktop/  — Electron 壳（NSIS 打包 + 自动更新）
- scripts/  — 构建 / 发布 / 校验脚本
- site/     — 官网静态页
- docs/     — 文档中心（guides / specs / plans / records / reports）

插件与工作流已剥离到独立仓库 `frameweave-plugins`（本仓库 `backend/user_nodes/` 保持空壳）。

## 快速开始
见 [docs/guides/开发指南.md](docs/guides/开发指南.md)（完整开发指南；文档索引见 [docs/README.md](docs/README.md)）

## 测试
```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
```
