"""manifest v2 强校验器（P1，t2）：G1-G10 校验门 + v1 兼容通道。

子模块：
- errors.py      —— 校验错误码与 PluginManifestError（显式错误码，不静默吞异常）
- manifest_v2.py —— G1-G10 门实现、v1 兼容注入、schemas/manifest_v2.json 结构校验

scan/install（declarative.py）通过 validate_manifest_file / validate_manifest 接入，
替换旧的 _validate_package 基础门。
"""
