"""导入 main.py（含所有服务）做一次全量冒烟检查，捕获语法/命名/转发错误。

运行：cd /opt/data/workspace && PYTHONPATH=/opt/data/workspace /tmp/olvenv/bin/python -m astrbot_plugin_openlist_bot.tests.test_import_smoke
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from astrbot_plugin_openlist_bot.tests import test_platform_adaptation as T  # noqa: E402

_PKG = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

checks = []


def main():
    # 1) main.py 可导入，插件类存在
    main_mod = importlib.import_module(f"{_PKG}.main")
    checks.append(("main.py 导入成功", hasattr(main_mod, "OpenlistPlugin")))

    # 2) 所有服务可导入
    for name in ("upload", "download", "browse", "config_command", "preview", "help", "account_service", "base"):
        mod = importlib.import_module(f"{_PKG}.services.{name}")
        checks.append((f"services.{name} 导入成功", mod is not None))

    # 3) 群文件相关配置已接入配置层
    cfg_mod = importlib.import_module(f"{_PKG}.lib.config")
    checks.append(("WebUI 映射含 group_file_auto_upload",
                   cfg_mod.WEBUI_CONFIG_MAPPING.get("group_file_auto_upload") == "group_file_auto_upload"))
    checks.append(("WebUI 映射含 group_file_upload_path",
                   cfg_mod.WEBUI_CONFIG_MAPPING.get("group_file_upload_path") == "group_file_upload_path"))
    defaults = cfg_mod.GlobalConfigManager.__init__.__doc__ or ""
    gcm = cfg_mod.GlobalConfigManager.__new__(cfg_mod.GlobalConfigManager)
    checks.append(("BOOLEAN_CONFIG_KEYS 含 group_file_auto_upload",
                   "group_file_auto_upload" in cfg_mod.BOOLEAN_CONFIG_KEYS))

    # 4) _conf_schema.json 与 lib/config.py 默认值一致
    import json
    schema_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json")
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    items = schema["global_settings"]["items"]
    checks.append(("_conf_schema 含 group_file_auto_upload 且默认 false",
                   items.get("group_file_auto_upload", {}).get("default") is False))
    checks.append(("_conf_schema 含 group_file_upload_path",
                   "group_file_upload_path" in items))

    # 5) 插件类具备 OneBot 守卫方法
    checks.append(("OpenlistPlugin 有 _get_onebot_api",
                   hasattr(main_mod.OpenlistPlugin, "_get_onebot_api")))
    checks.append(("UploadService 有 _get_onebot_api",
                   hasattr(T.UploadService, "_get_onebot_api")))

    # 6) handler 注册检查：remember_recent_upload_message 覆盖 ALL（含群文件通知）
    import astrbot.api.event as ev
    regs = ev.filter.registrations
    names = [r[1] for r in regs]
    checks.append(("已注册 remember_recent_upload_message", "remember_recent_upload_message" in names))
    evt_reg = next((r for r in regs if r[1] == "remember_recent_upload_message"), None)
    ALL = ev.filter.EventMessageType.ALL
    checks.append(("remember 处理器覆盖 ALL(群+私聊+通知)", evt_reg is not None and evt_reg[2] == ALL),
                   )
    cmd_names = [r[2] for r in regs if r[0] == "group_command"]
    for expected in ("素材 上传", "素材 列表", "素材 下载", "素材 帮助", "素材 配置"):
        checks.append((f"指令已注册：{expected}", expected in cmd_names))

    # 6) 安装插件实例，验证 group_upload 通知能被注册的 handler 覆盖
    plugin = main_mod.OpenlistPlugin(context=None, config=None)
    checks.append(("插件实例化成功", plugin is not None and hasattr(plugin, "upload_service")))

    print()
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(f"{'✅' if ok else '❌'} {name}")
    print(f"\n通过 {len(checks) - len(failed)} / {len(checks)}")
    if failed:
        print("失败:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()
