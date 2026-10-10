"""平台插件加载器安全共享模块（P0.4）。

worker/limits.py 是上传/解压参数的唯一引用点，server 上传、商城安装、
declarative.install_zip 三入口共享，避免参数旁路（§6.1）。
"""