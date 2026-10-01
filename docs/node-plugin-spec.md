# 拾帧 FrameWeave · 节点插件规范 v1.0

> 目标：第三方节点插件生态。后端基建已完备（M16 声明式），本文档定义插件结构、安装、识别与分发。

## 1. 插件包结构

```
user_nodes/<pkg>/            # 每个子目录 = 一个插件包
├── manifest.json            # 必需：声明文件（唯一识别入口）
└── （可选资源：脚本/数据，transform 节点一般不需要）
```

目录位置：
- 开发环境：`backend/user_nodes/`
- 打包版：`%APPDATA%\FrameWeave\user_nodes/`（server 启动时由 declarative.set_user_nodes_dir 指向，可写、升级保留）
- 可用 `FRAMEWEAVE_USER_NODES` 环境变量覆盖

## 2. manifest.json 规范

```json
{
  "type_id": "user/watermark",        // 全局唯一：pkg/节点名（斜杠分隔，引擎按 pkg 分目录）
  "title": "水印叠加",
  "category": "输出",                  // 输入/语义/分析/控制/输出/用户节点...
  "description": "在视频上叠加文字水印",
  "version": "1.0.0",
  "kind": "transform",                 // 当前仅 transform（白名单纯数据变换）
  "gpu_required": false,
  "gpu_recommended": false,
  "streaming": false,
  "inputs":  [{ "name": "text", "type": "STRING", "label": "文本", "required": true, "description": "要叠加的文字" }],
  "outputs": [{ "name": "out", "type": "STRING", "label": "结果" }],
  "params":  [{ "name": "position", "type": "STRING", "label": "位置", "widget": "select", "options": ["tl","tr","bl","br"], "default": "tr" }],
  "transform": {                        // kind=transform：输出端口 -> 运算表达式
    "out": { "$concat": [{ "$input": "text" }, { "$const": " @ " }, { "$param": "position" }] }
  }
}
```

## 3. 字段与类型

| 字段 | 必填 | 说明 |
|---|---|---|
| type_id | ✓ | 全局唯一 `pkg/节点名`；不合法（无斜杠/重复/保留前缀 core/）→ 拒绝 |
| title / category / description | ✓ | 规格展示；category 决定侧栏/添加菜单分组 |
| version | | 语义化版本 |
| kind | ✓ | 仅 `transform`；其它值拒绝（未来扩展 code/python 需沙箱评审） |
| inputs/outputs | | PortSpec 数组：name/type/label/required/default/widget/options/description |
| params | | 参数表单元数据（前端 ParamPanel 渲染）：widget=text|number|select|file|toggle |
| transform | kind=transform 必填 | 输出端口 → op 表达式 |

端口类型（PortType 枚举）：VIDEO / IMAGE / AUDIO / SCRIPT / SUBTITLE / SEGMENTS / TIMELINE / JSON / STRING / INT / FLOAT / BOOL / ANY

## 4. transform 表达式（白名单，不执行任意代码）

取值源：
- `{"$param": "名"}` → 参数值
- `{"$input": "端口"}` → 输入端口值
- `{"$const": 值}` → 常量
- `{"$list": [..]}` → 数组字面量

顶层运算 op（白名单全集）：
- concat（拼接） / json_get（JSON 取字段） / math（算术） / str（格式化） / list（数组） / upper / lower / len

安全模型：仅纯数据变换（字符串/数字/JSON），**不执行用户代码**；越界 op 一律拒绝。

## 5. 识别与加载（引擎侧）

1. 启动 / `POST /api/user_nodes/reload` → `declarative.scan()` 遍历 USER_NODES_DIR 子目录
2. 每个目录读 `manifest.json` → 校验字段与白名单 → 注册为 NodeSpec（与内置 `nodespec.NodeSpec` 同构）
3. 注册进 specs 表 → `GET /api/specs` 下发前端（Sidebar/右键菜单/节点卡共用）
4. 执行时 `get_class(type_id)` 取声明类，白名单求值

## 6. 安装方式（三种入口）

| 方式 | 接口 | 说明 |
|---|---|---|
| 本地 zip 导入 | `POST /api/user_nodes/install`（multipart file） | 解压校验 → 重扫；**前端导入 UI 待补（本方案）** |
| 商城安装 | `POST /api/market/items/{id}/install` | 付费/免费下载，扣积分，分成 20/80 |
| 网盘直链 | market item 的 download_url（百度/123 直链） | 发布者分发 |

## 7. 分发与发布

- 导出：`GET /api/export/node/{type_id}` → 打包 `<pkg>.zip` 下载（可再传商城/网盘）
- 发布：MarketPage「发布」表单（kind=node / 价格 / 描述 / 标签 / download_url 或内联 zip）
- 缺节点引导：打开工作流时若 spec 缺失 → 前端据 type_id 查商城（`/api/market/items?q=`）提示安装

## 8. 校验规则（识别方法要点）

- manifest 缺/坏 → 拒绝安装并报具体原因
- type_id 冲突（与内置或已装插件重复）→ 拒绝，提示改名
- kind 非 transform → 拒绝（未来版本开放 code 沙箱再扩展）
- op 非白名单 → 拒绝
