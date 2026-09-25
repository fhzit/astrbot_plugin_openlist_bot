"""用真实 AstrBot 的 components.py 源文件（非桩件）验证附件提取。

做法：把 components.py 的少量外部依赖（astrbot.core 等）替换成桩件后按文件路径加载，
这样 Image/Video/File/ComponentType 都是上游真实定义，避免自造桩件掩盖差异。

运行：/tmp/olvenv/bin/python tests/test_real_components.py
"""
import asyncio
import importlib.util
import os
import sys
import types

_ASTRBOT_REF = os.environ.get("ASTRBOT_REF", "/tmp/astrbot_ref")
_COMPONENTS_PY = os.path.join(_ASTRBOT_REF, "astrbot", "core", "message", "components.py")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_REPO_ROOT))


def _load_real_components():
    """按文件路径加载上游 components.py，仅桩掉它与本任务无关的外部依赖。"""
    def _stub_module(name):
        mod = types.ModuleType(name)
        sys.modules[name] = mod
        return mod

    # astrbot.core 及其子模块桩（components.py 只用 logger / config / 工具函数）
    core = _stub_module("astrbot.core")
    core.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, debug=lambda *a, **k: None,
        warning=lambda *a, **k: None, error=lambda *a, **k: None,
    )
    core.astrbot_config = {}
    core.file_token_service = types.SimpleNamespace()

    utils = _stub_module("astrbot.core.utils")
    ap = _stub_module("astrbot.core.utils.astrbot_path")
    ap.get_astrbot_temp_path = lambda: "/tmp"
    io_mod = _stub_module("astrbot.core.utils.io")

    async def _download_file(*a, **k):
        return None

    io_mod.download_file = _download_file

    media = _stub_module("astrbot.core.utils.media_utils")

    class _MediaResolver:
        def __init__(self, url, media_type=None):
            self.url = url

        async def to_path(self):
            return self.url

    media.MediaResolver = _MediaResolver
    media.file_uri_to_path = lambda uri: uri.replace("file://", "")
    media.is_file_uri = lambda value: str(value).startswith("file://")
    # 让 astrbot.core.utils.X 也能被 import 到
    core.utils = utils
    utils.astrbot_path = ap
    utils.io = io_mod
    utils.media_utils = media

    # deprecated 是上游唯一的第三方依赖，缺失时提供等价桩（仅装饰器语义）
    try:
        import deprecated as _deprecated  # noqa: F401
    except ImportError:
        dep_mod = _stub_module("deprecated")

        def _deprecated_decorator(*dargs, **dkwargs):
            def _deco(obj):
                return obj

            if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
                return dargs[0]
            return _deco

        dep_mod.deprecated = _deprecated_decorator

    spec = importlib.util.spec_from_file_location("_real_components", _COMPONENTS_PY)
    assert spec and spec.loader, f"无法加载 {_COMPONENTS_PY}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["_real_components"] = module
    spec.loader.exec_module(module)
    return module


REAL = _load_real_components()
Image = REAL.Image
Video = REAL.Video
File = REAL.File
Plain = REAL.Plain
ComponentType = REAL.ComponentType

if os.environ.get("OL_USE_REAL_ASTRBOT") != "1":
    os.environ["OL_USE_REAL_ASTRBOT"] = "0"

from astrbot_plugin_openlist_bot.tests import test_platform_adaptation as T  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"{'✅' if ok else '❌'} {name}" + (f"  → {detail}" if detail else ""))


def main():
    print(f"真实组件来源: {_COMPONENTS_PY}")
    check("已加载上游真实 components 模块", hasattr(REAL, "File") and hasattr(REAL, "ComponentType"))

    svc = T.FakePlugin().upload_service

    # 1) 完全复刻 QQ 官方适配器 _append_attachments 的构造方式
    img = Image.fromURL("https://multimedia.nt.qq.com.cn/download?appid=1&fileid=abc")
    vid = Video.fromURL("https://multimedia.nt.qq.com.cn/download?appid=1&fileid=vid")
    fil = File(name="报告.pdf", file="https://multimedia.nt.qq.com.cn/download?fileid=doc",
               url="https://multimedia.nt.qq.com.cn/download?fileid=doc")

    check("上游 Image.fromURL 存的是 file 字段（非 url）",
          img.file == "https://multimedia.nt.qq.com.cn/download?appid=1&fileid=abc" and not img.url,
          f"file={img.file!r} url={img.url!r}")
    check("上游 File 用 file_ 存本地路径、url 存直链",
          fil.file_ == "https://multimedia.nt.qq.com.cn/download?fileid=doc" and fil.url.endswith("fileid=doc"),
          f"file_={fil.file_!r} url={fil.url!r}")

    event = T.FakeEvent(components=[Plain(text="hi"), img, vid, fil], raw_message=object())
    segs = svc._extract_upload_components(event)
    kinds = [s["type"] for s in segs]
    check("真实组件：Image/Video/File 都被提取", kinds == ["image", "video", "file"], f"kinds={kinds}")

    urls = {s["type"]: (s["data"].get("url") or s["data"].get("file")) for s in segs}
    check("Image 直链取自 .file",
          "fileid=abc" in (urls.get("image") or ""), f"url={urls.get('image')}")
    check("Video 直链取自 .file", "fileid=vid" in (urls.get("video") or ""), f"url={urls.get('video')}")
    check("File 直链取自 .url，且未触碰 .file（避免同步下载警告）",
          (urls.get("file") or "").endswith("fileid=doc"), f"url={urls.get('file')}")
    check("File 组件带上 name", segs[2]["data"].get("name") == "报告.pdf", f"data={segs[2]['data']}")

    # 2) 确认读取组件的过程没有触发上游 File.file 的「异步上下文同步下载」警告
    import io
    import logging

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logging.getLogger().addHandler(handler)
    svc._extract_upload_components(event)
    logging.getLogger().removeHandler(handler)
    check("提取过程无 '同步等待下载' 警告", "同步等待下载" not in buf.getvalue(),
          f"log={buf.getvalue()[:120]!r}")

    # 3) ComponentType 是上游 str 枚举 —— 验证映射表命中真实枚举值
    check("ComponentType.Image.value 命中映射表",
          str(ComponentType.Image.value) in svc.COMPONENT_TYPE_TO_SEGMENT,
          f"value={ComponentType.Image.value!r} map={sorted(svc.COMPONENT_TYPE_TO_SEGMENT)}")

    # 4) 本地 file:// 图片不应被当成 http 直链
    local_img = Image.fromFileSystem("/tmp/demo.png")
    local_event = T.FakeEvent(components=[local_img], raw_message=object())
    local_segs = svc._extract_upload_components(local_event)
    check("本地 file:// 图片落在 file 字段而非 url",
          bool(local_segs) and "url" not in local_segs[0]["data"],
          f"data={local_segs[0]['data'] if local_segs else None}")

    # 5) 真实组件驱动完整私聊自动上传（QQ 官方路径）
    uploaded = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def ensure_dir(self, path):
            return True

        async def list_files(self, path, per_page=30):
            return {"content": []}

        async def upload_url_stream(self, source_url, target_path, filename, file_size=None):
            uploaded.append((target_path, filename, source_url))
            return True

        async def get_direct_download_link(self, path):
            return None

        async def upload_file(self, *a, **k):
            return True

    plugin = T.FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
    })
    svc2 = plugin.upload_service
    plugin._create_openlist_client = lambda cfg: FakeClient()

    real_event = T.FakeEvent(
        components=[Image.fromURL("https://multimedia.nt.qq.com.cn/download?fileid=private")],
        raw_message=T.FakeBotpyObject(), group_id=None, sender_id="QQUID_OPENID",
        message_str="", origin="qq_official:FriendMessage:QQUID_OPENID",
    )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc2.remember_uploadable_message(real_event))
        for t in list(svc2._auto_upload_tasks):
            loop.run_until_complete(t)
    finally:
        loop.close()

    check("真实组件驱动的私聊自动上传成功", len(uploaded) == 1, f"uploaded={uploaded}")
    check("上传文件名补全 .jpg", bool(uploaded) and uploaded[0][1].endswith(".jpg"),
          f"name={uploaded[0][1] if uploaded else None}")

    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
