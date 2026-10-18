# frameweave.top 官网部署记录（2026-10-18）

## 1. 站点

- 地址：https://frameweave.top/ （HTTP 301 → HTTPS）
- 本地源码：`FrameWeave/site/official/`（`index.html` + `assets/`，11 个资产）
- 服务器目录：`/opt/1panel/www/sites/frameweave.top/{index,ssl,log}`
- 容器映射：`/opt/1panel/www` → `/www`；站点根 `/www/sites/frameweave.top/index`

## 2. 配置

- Nginx 站点：`/opt/1panel/www/conf.d/frameweave.top.conf`
- 备份：`frameweave.top.conf.bak_http_20261018`（ACME 前仅 HTTP 版本）
- 要点：80/443 双监听、`http2 on`、HTTP→HTTPS 301、TLS1.2/1.3、HSTS、`error_page 497`、
  `/.well-known/acme-challenge` 保留（root `/usr/share/nginx/html`）、
  gzip（text/css/js/json/svg/xml）、图片 7 天缓存（文件名可能被替换，未用 immutable）、
  `index.html` no-cache、`X-Content-Type-Options` / `Referrer-Policy` / `X-Frame-Options`。
- 未改动 `houtai` / `shouquan` / `xui` 的任何配置与文件；`nginx -t` 通过后 reload。

## 3. 证书

- Let's Encrypt（ACME http-01），签发脚本 `.tmp/acme_frameweave.py`
- `/opt/1panel/www/sites/frameweave.top/ssl/fullchain.pem`、`privkey.pem`（600）
- issuer：`CN=YR2, O=Let's Encrypt, C=US`
- subject：`CN=frameweave.top`；SAN：`frameweave.top`
- 有效期：2026-10-18 → 2027-01-16
- TLS：TLSv1.3 / TLS_AES_256_GCM_SHA384，链验证通过

## 4. 验证

| 项目 | 结果 |
|---|---|
| `curl -I https://frameweave.top/` | 200 OK |
| HTTP 跳转 | 301 → https |
| 证书 issuer / 日期 / SAN | Let's Encrypt YR2，2026-10-18 ~ 2027-01-16，frameweave.top |
| Playwright 1440×900 / 1024×768 / 768×1024 / 390×844 | 无 JS 错误、无横向滚动、图片全部加载 |
| 移动端菜单 | 打开/关闭、`aria-expanded`、ESC 关闭正常 |
| 外链请求 | 仅 GitHub 与下载域名，无 CDN / 外部字体 / 外部 JS |
| 下载链接 | `FrameWeave-Setup-0.3.12.exe` 200，214 MiB |

## 5. 截图

本目录下：

- `desktop-1440-fold.png` / `desktop-1440-*.png` / `desktop-1440-full.png`
- `desktop-1024-*.png`
- `tablet-768-*.png`
- `mobile-390-fold.png` / `mobile-390-*.png` / `mobile-390-full.png`
- `mobile-menu.png`
- `faq-open.png`
- `report.json`（尺寸、溢出、控制台错误、菜单状态等原始检测结果）

## 6. 说明

- 首屏主图为终版 `workbench.png`（1600×1000）；`inspector.png` / `library.png` / `login.png` / `market.png`
  分别用于界面与生态章节；文件名保持不变，后续可原样覆盖。
- `pipeline.png` 为 v0.3.12 工作台节点画布的局部裁切，用于流水线章节配图。
- 本地工作机时钟比标准时间慢约 1 小时（与本机 `date -u` 对照 baidu/cloudflare 响应头验证）；
  证书本身有效期正确，与本机时钟偏差无关。
