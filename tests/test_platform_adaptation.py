"""用桩件验证群文件上传与 QQ 官方机器人适配逻辑（不依赖真实 AstrBot 安装）。

运行：/tmp/olvenv/bin/python tests/test_platform_adaptation.py
"""
import asyncio
import importlib
import os
import sys
import tempfile
import types
from enum import Enum

# ---------------------------------------------------------------- astrbot stubs
DATA_DIR = tempfile.mkdtemp(prefix="ol_test_data_")


class _Logger:
    def __getattr__(self, name):
        def _log(*args, **kwargs):
            if name in ("warning", "error"):
                print(f"[{name}]", *args)
        return _log


class _StarTools:
    @staticmethod
    def get_data_dir(name=""):
        return DATA_DIR


class _Star:
    def __init__(self, context=None, config=None):
        self.context = context
        self.config = config


def _install_astrbot_stubs():
    # 若显式要求使用真实 AstrBot（OL_USE_REAL_ASTRBOT=1），则跳过桩件安装，
    # 这样能在真实组件类上验证适配逻辑（比桩件更强）。
    if os.environ.get("OL_USE_REAL_ASTRBOT") == "1":
        try:
            real_comp = importlib.import_module("astrbot.api.message_components")
            real_event = importlib.import_module("astrbot.api.event")
            real_event.filter.EventMessageType  # noqa: B018 - 触发真实模块校验
            return real_comp
        except Exception as e:  # pragma: no cover - 环境缺失时退回桩件
            print(f"[warn] 无法使用真实 AstrBot，退回桩件: {e}")

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _Logger()
    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = object
    star_mod.Star = _Star
    star_mod.StarTools = _StarTools
    event_mod = types.ModuleType("astrbot.api.event")

    class AstrMessageEvent:
        pass

    class MessageChain:
        def __init__(self, chain=None, **kwargs):
            self.chain = chain or []

        def message(self, text):
            self.chain.append(text)
            return self

    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain

    # filter 桩：记录注册信息，便于校验 handler 挂载
    registered = []

    class _FilterNamespace:
        def __init__(self):
            self.registrations = registered

        def event_message_type(self, event_message_type, **kwargs):
            def deco(func):
                registered.append(("event_message_type", func.__name__, event_message_type, kwargs))
                return func
            return deco

        def command(self, name, alias=None, **kwargs):
            def deco(func):
                registered.append(("command", func.__name__, name, alias))
                return func
            return deco

        def command_group(self, name, sub_command=None, alias=None, **kwargs):
            outer = self

            class _RegisteringCommandable:
                def __init__(self, group_name, parent=None):
                    self.group_name = group_name
                    self.parent = parent

                def command(self, sub_name=None, alias=None, **kwargs):
                    def deco(func):
                        full = f"{self.group_name} {sub_name}" if sub_name else self.group_name
                        registered.append(("group_command", func.__name__, full, alias))
                        return func
                    return deco

                def command_group(self, sub_name, alias=None, **kwargs):
                    return _RegisteringCommandable(f"{self.group_name} {sub_name}", self)

            def deco(obj):
                return _RegisteringCommandable(name)

            return deco

        def regex(self, pattern, **kwargs):
            def deco(func):
                return func
            return deco

        def platform_adapter_type(self, *a, **k):
            def deco(func):
                return func
            return deco

        def llm_tool(self, *a, **k):
            def deco(func):
                return func
            return deco

        def on_decorating_result(self, **kwargs):
            def deco(func):
                return func
            return deco

        def event_message(self, **kwargs):
            def deco(func):
                registered.append(("event_message", func.__name__, kwargs))
                return func
            return deco

        def permission_type(self, *a, **k):
            def deco(func):
                return func
            return deco

        def on_astrbot_loaded(self, **kwargs):
            def deco(func):
                return func
            return deco

        def on_plugin_loaded(self, **kwargs):
            def deco(func):
                return func
            return deco

    class EventMessageType:
        GROUP_MESSAGE = 1
        PRIVATE_MESSAGE = 2
        OTHER_MESSAGE = 4
        ALL = 7

    filter_ns = _FilterNamespace()
    filter_ns.EventMessageType = EventMessageType
    event_mod.filter = filter_ns

    comp_mod = types.ModuleType("astrbot.api.message_components")

    class _Type(str):
        """模拟 AstrBot ComponentType：str 子类，.value 返回类型名。"""

        @property
        def value(self):
            return str(self)

    def _type_of(value):
        return _Type(value)

    class Image:
        def __init__(self, file="", url="", **kwargs):
            self.file = file
            self.url = url
            self.type = _type_of("Image")

    class Video:
        def __init__(self, file="", url="", **kwargs):
            self.file = file
            self.url = url
            self.type = _type_of("Video")

    class File:
        def __init__(self, name="", file="", url="", **kwargs):
            self.name = name
            self.file_ = file
            self.url = url
            self.type = _type_of("File")

    comp_mod.Image = Image
    comp_mod.Video = Video
    comp_mod.File = File
    comp_mod.Plain = type("Plain", (), {})

    sys.modules.update({
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.star": star_mod,
        "astrbot.api.event": event_mod,
        "astrbot.api.message_components": comp_mod,
    })
    return comp_mod


COMP = _install_astrbot_stubs()
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(_REPO_ROOT))
_PKG = os.path.basename(_REPO_ROOT)
from importlib import import_module  # noqa: E402

UploadService = import_module(f"{_PKG}.services.upload").UploadService


def _close_coro(coro):
    """关闭未 await 的协程，避免测试替身留下 warning。"""
    try:
        coro.close()
    except Exception:
        pass


class FakeTask:
    """可哈希的假任务对象，替代 asyncio.Task 用于调度断言。"""

    def add_done_callback(self, cb):
        return None


# ------------------------------------------------------------------ fake event
class FakeMessageObj:
    def __init__(self, **kw):
        self.raw_message = kw.get("raw_message")
        self.group_id = kw.get("group_id")
        self.message = kw.get("message")
        self.sender = kw.get("sender")
        self.session_id = kw.get("session_id", "")
        self.self_id = kw.get("self_id", "10000")


class FakeResult:
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return f"<Result {self.text[:80]!r}>"


class FakeEvent:
    def __init__(self, raw_message=None, group_id=None, components=None,
                 sender_id="12345", message_str="", bot=None, origin=""):
        self.message_obj = FakeMessageObj(raw_message=raw_message, group_id=group_id)
        self._components = components or []
        self._sender_id = sender_id
        self.message_str = message_str
        self.bot = bot
        self.unified_msg_origin = origin

    def get_messages(self):
        return self._components

    def get_sender_id(self):
        return self._sender_id

    def plain_result(self, text):
        return FakeResult(text)

    def chain_result(self, chain):
        return FakeResult(str(chain))


# ------------------------------------------------------------------- fake bot
class FakeBotpyObject:
    """模拟 botpy.message.GroupMessage（非 dict 的 raw_message）。"""
    def __init__(self, group_openid="G-OPENID-1"):
        self.group_openid = group_openid
        self.id = "msg-1"


class FakePlugin:
    """最小插件替身，提供 service 需要的转发方法。"""

    def __init__(self, global_cfg=None):
        self.recent_upload_messages = {}
        self.user_navigation_state = {}
        self.cache_manager = types.SimpleNamespace(clear_cache=lambda *a, **k: None)
        self.context = types.SimpleNamespace(send_message=self._noop_async)
        self._global_cfg = global_cfg or {
            "require_user_auth": False,
            "openlist_url": "http://openlist.local:5244",
            "max_upload_size": 100,
            "group_file_auto_upload": True,
            "group_file_upload_path": "/群文件",
        }
        self.upload_service = UploadService(self)
        self.created_clients = 0
        self.sent_messages = []

    async def _noop_async(self, *a, **k):
        return None

    # ---- 插件侧工具转发
    def get_global_config(self):
        return self._global_cfg

    def get_user_config(self, user_id):
        return self._global_cfg

    def _validate_config(self, cfg):
        return bool(cfg.get("openlist_url"))

    def _get_navigation_state_key(self, event):
        gid = self._get_event_group_id(event)
        if gid not in (None, ""):
            return f"group:{gid}:user:{event.get_sender_id()}"
        return f"private:user:{event.get_sender_id()}"

    def _get_event_group_id(self, event):
        gid = getattr(getattr(event, "message_obj", None), "group_id", None)
        if gid not in (None, ""):
            return gid
        return None

    def _read_value(self, obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _normalize_openlist_path(self, path):
        p = (path or "").strip().replace("\\", "/")
        if not p:
            return "/"
        if not p.startswith("/"):
            p = "/" + p
        while "//" in p:
            p = p.replace("//", "/")
        return p.rstrip("/") or "/"

    def _sanitize_filename(self, name, fallback="file"):
        safe = "".join(c for c in (name or "") if c.isalnum() or c in "._- ").strip(" .")
        return safe[:100] or fallback

    def _unique_suffix(self):
        import uuid
        return uuid.uuid4().hex[:12]

    def _get_size_limit_mb(self, cfg, key, default):
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _get_positive_int_config(self, cfg, key, default, minimum=1):
        try:
            v = int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default
        return v if v >= minimum else default

    def _get_bool_config(self, cfg, key, default=False):
        v = cfg.get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        return bool(v)

    def _is_extension_allowed(self, filename, cfg, key="allowed_extensions"):
        return True

    def _format_extension_filter(self, cfg, key="allowed_extensions"):
        return "不限制"

    def _get_retry_config(self, cfg, prefix):
        return (1, 0)

    def is_submit_mode(self, cfg):
        return bool((cfg.get("default_submit_path") or "").strip())

    def get_submit_root(self, cfg):
        return self._normalize_openlist_path((cfg.get("default_submit_path") or "").strip())

    def get_user_submit_dir(self, root, sender_id):
        return self._normalize_openlist_path(f"{root}/{sender_id}")

    def _create_openlist_client(self, cfg):
        raise AssertionError("测试不应真正创建 OpenList 客户端")

    def _format_file_size(self, size):
        return f"{size}B"

    def _format_usage_tip(self, title, usage, examples=None, note=""):
        return title

    def _update_user_navigation_state(self, user_id, path, items):
        self.user_navigation_state.setdefault(user_id, {})["current_path"] = path

    def _format_file_list(self, files, current_path, user_config, user_id=None):
        return f"📁 {current_path}（{len(files)} 项）"

    def _resolve_target_path(self, user_id, path, default_to_current=True):
        nav = self.user_navigation_state.get(user_id) or {"current_path": "/"}
        base = nav.get("current_path", "/")
        raw = (path or "").strip()
        if not raw:
            return base if default_to_current else "/"
        if raw.startswith("/"):
            return self._normalize_openlist_path(raw)
        return self._normalize_openlist_path(f"{base.rstrip('/')}/{raw}")

    def _format_upload_usage_tip(self, title):
        return title


# ---------------------------------------------------------------------- tests
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))


def t1_group_upload_notice():
    plugin = FakePlugin()
    svc = plugin.upload_service
    notice = {
        "post_type": "notice",
        "notice_type": "group_upload",
        "group_id": 888888,
        "user_id": 12345,
        "self_id": 10000,
        "file": {"id": "file-abc", "name": "报告.pdf", "size": 2048, "busid": 102},
    }
    event = FakeEvent(raw_message=notice, group_id=888888)
    check("1.1 识别 group_upload 通知", svc._is_group_file_upload_notice(event))
    segments = svc._segments_from_group_upload_notice(event)
    check("1.2 通知解析出 file 段", len(segments) == 1 and segments[0]["type"] == "file",
          f"segments={segments}")
    data = segments[0]["data"] if segments else {}
    check("1.3 保留 file_id/名称/大小/busid",
          data.get("file_id") == "file-abc" and data.get("name") == "报告.pdf"
          and data.get("file_size") == 2048 and data.get("busid") == 102,
          f"data={data}")

    # 调度自动转存（配置已开启）→ 验证去重：拦截 asyncio.create_task 计数
    cached = {"group_id": 888888, "message_id": None, "message": segments,
              "is_group_upload_notice": True}
    created = []
    original_create_task = asyncio.create_task

    def _fake_create_task(coro):
        created.append(coro)
        _close_coro(coro)
        return FakeTask()

    asyncio.create_task = _fake_create_task
    try:
        svc._schedule_group_file_auto_upload(event, cached)
        svc._schedule_group_file_auto_upload(event, cached)
    finally:
        asyncio.create_task = original_create_task
    check("1.4 同一群文件只调度一次（去重）", len(created) == 1, f"created={len(created)}")


def t2_botpy_object_components():
    """QQ 官方机器人：raw_message 是 botpy 对象，附件在 AstrBot 组件里。"""
    plugin = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
    })
    svc = plugin.upload_service
    components = [
        COMP.Image(file="https://multimedia.nt.qq.com.cn/download?img=1"),
        COMP.File(name="素材.zip", file="https://multimedia.nt.qq.com.cn/download?file=2",
                  url="https://multimedia.nt.qq.com.cn/download?file=2"),
        COMP.Video(file="https://multimedia.nt.qq.com.cn/download?video=3"),
    ]
    raw = FakeBotpyObject()
    event = FakeEvent(raw_message=raw, group_id="G-OPENID-1", components=components)

    segments = svc._extract_upload_components(event)
    check("2.1 从组件提取 3 个附件", len(segments) == 3, f"segments={segments}")
    types_found = [s["type"] for s in segments]
    check("2.2 类型映射为 image/file/video", types_found == ["image", "file", "video"], f"{types_found}")
    file_seg = next((s for s in segments if s["type"] == "file"), {})
    check("2.3 File 组件保留文件名与 URL",
          file_seg.get("data", {}).get("name") == "素材.zip"
          and file_seg.get("data", {}).get("url", "").startswith("https://"),
          f"{file_seg}")

    # 完整走 remember_uploadable_message
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc.remember_uploadable_message(event))
    finally:
        loop.close()
    cached = plugin.recent_upload_messages.get("group:G-OPENID-1:user:12345")
    check("2.4 非 dict raw_message 也记录到最近附件缓存", cached is not None,
          f"keys={list(plugin.recent_upload_messages)}")


def t3_qq_official_private():
    """QQ 官方 C2C 私聊：无 group_id → 走私聊自动上传分支。"""
    plugin = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
    })
    svc = plugin.upload_service
    scheduled = []
    svc._schedule_private_auto_upload = lambda ev, cm: scheduled.append(cm)  # type: ignore

    components = [COMP.File(name="投稿.mp4", file="https://x.qcloud.com/f.mp4")]
    raw = FakeBotpyObject()
    event = FakeEvent(raw_message=raw, group_id=None, components=components, sender_id="user-openid-9")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc.remember_uploadable_message(event))
    finally:
        loop.close()
    check("3.1 C2C 私聊触发私聊自动上传", len(scheduled) == 1, f"scheduled={len(scheduled)}")
    check("3.2 私聊会话键使用 openid",
          "private:user:user-openid-9" in plugin.recent_upload_messages,
          f"{list(plugin.recent_upload_messages)}")


def t4_group_file_url_no_onebot():
    """QQ 官方机器人没有 OneBot API，群文件 URL 解析必须安全返回 None。"""
    plugin = FakePlugin()
    svc = plugin.upload_service
    event = FakeEvent(raw_message=FakeBotpyObject(), group_id="G-1",
                      bot=types.SimpleNamespace())  # bot 无 api 属性
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            svc._get_group_file_url(event, "G-1", "file-x", 0))
        private = loop.run_until_complete(
            svc._resolve_private_file_url(event, {"data": {"file_id": "f1"}}, "u1"))
    finally:
        loop.close()
    check("4.1 无 OneBot API 时群文件 URL 返回 None（不抛异常）", result is None, f"{result}")
    check("4.2 无 OneBot API 时私聊文件 URL 返回 None", private is None, f"{private}")


def t5_onebot_regression():
    """回归：OneBot 普通消息的 image/file 段仍能解析（未被新逻辑破坏）。"""
    plugin = FakePlugin()
    svc = plugin.upload_service
    message = [
        {"type": "text", "data": {"text": "给"}},
        {"type": "image", "data": {"file": "abc.jpg", "url": "https://gchat.qpic.cn/a.jpg"}},
        {"type": "file", "data": {"file_id": "fid-1", "file": "doc.pdf", "file_size": 5}},
    ]
    raw = {"post_type": "message", "message_type": "group", "group_id": 777,
           "user_id": 12345, "self_id": 10000, "message_id": 9001, "message": message}
    event = FakeEvent(raw_message=raw, group_id=777)
    check("5.1 非 notice 不被误判为群文件通知", not svc._is_group_file_upload_notice(event))
    segments = svc._extract_upload_segments({"message": message})
    check("5.2 OneBot 消息段解析保持可用",
          [s["type"] for s in segments] == ["image", "file"], f"{segments}")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc.remember_uploadable_message(event))
    finally:
        loop.close()
    cached = plugin.recent_upload_messages.get("group:777:user:12345")
    check("5.3 普通群消息仍记录最近附件", cached is not None)
    check("5.4 普通群消息不触发群文件自动转存",
          cached is not None and cached["message"].get("is_group_upload_notice") is False,
          f"{cached}")


def t6_group_file_disabled_by_default():
    plugin = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "group_file_auto_upload": False,
    })
    svc = plugin.upload_service
    created = []
    original_create_task = asyncio.create_task

    def _fake_create_task(coro):
        created.append(coro)
        _close_coro(coro)
        return FakeTask()

    asyncio.create_task = _fake_create_task
    notice = {"post_type": "notice", "notice_type": "group_upload", "group_id": 888888,
              "user_id": 12345, "self_id": 10000,
              "file": {"id": "f-1", "name": "a.txt", "size": 10}}
    event = FakeEvent(raw_message=notice, group_id=888888)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc.remember_uploadable_message(event))
    finally:
        loop.close()
        asyncio.create_task = original_create_task
    check("6.1 开关关闭时不自动转存（但仍缓存供 素材 上传）",
          not created and "group:888888:user:12345" in plugin.recent_upload_messages,
          f"created={len(created)}")


def t7_end_to_end_group_file_upload():
    """端到端：群文件通知 → 自动转存 → 真的调用 OpenList 上传（用假客户端断言）。"""
    plugin = FakePlugin()
    svc = plugin.upload_service
    uploaded = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def ensure_dir(self, path):
            if path not in ("", "/"):
                uploaded.append(("ensure_dir", path))
            return True

        async def upload_url_stream(self, source_url, target_path, filename, file_size=None):
            uploaded.append(("upload", target_path, filename, source_url, file_size))
            return True

    plugin._create_openlist_client = lambda cfg: FakeClient()
    svc.get_user_config = lambda uid: plugin._global_cfg  # type: ignore

    async def fake_group_file_url(event, group_id, file_id, busid=0):
        return f"https://gchat.qpic.cn/gchatpic_new/{file_id}"

    svc._get_group_file_url = fake_group_file_url  # type: ignore

    notice = {"post_type": "notice", "notice_type": "group_upload", "group_id": 888888,
              "user_id": 12345, "self_id": 10000,
              "file": {"id": "fid-777", "name": "会议纪要.docx", "size": 4096, "busid": 102}}
    event = FakeEvent(raw_message=notice, group_id=888888, origin="aiocqhttp:GroupMessage:888888")
    cached = {
        "group_id": 888888,
        "message_id": None,
        "message": svc._segments_from_group_upload_notice(event),
        "is_group_upload_notice": True,
    }
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            svc._auto_upload_group_file(event, cached, "/群文件", "12345"))
    finally:
        loop.close()

    uploads = [u for u in uploaded if u[0] == "upload"]
    check("7.1 群文件真的上传到 OpenList", len(uploads) == 1, f"uploaded={uploaded}")
    if uploads:
        _, target, name, url, size = uploads[0]
        check("7.2 目标目录与文件名正确",
              target == "/群文件" and name == "会议纪要.docx", f"{target}/{name}")
        check("7.3 使用 group_file_url 解析出的直链",
              url.endswith("/fid-777"), f"url={url}")
        check("7.4 大小来自通知", size == 4096, f"size={size}")
    check("7.5 自动创建目标目录", ("ensure_dir", "/群文件") in uploaded, f"{uploaded}")


def t8_end_to_end_qq_official_private_upload():
    """端到端：QQ 官方私聊附件 → 自动上传到日期目录（组件直链直接可用）。"""
    plugin = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
    })
    svc = plugin.upload_service
    uploaded = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def ensure_dir(self, path):
            return True

        async def upload_url_stream(self, source_url, target_path, filename, file_size=None):
            uploaded.append((target_path, filename, source_url))
            return True

        async def list_files(self, path, per_page=30):
            return {"content": []}

    plugin._create_openlist_client = lambda cfg: FakeClient()

    components = [COMP.File(name="投稿视频.mp4",
                            file="https://multimedia.nt.qq.com.cn/download?file=xyz")]
    raw = FakeBotpyObject()
    event = FakeEvent(raw_message=raw, group_id=None, components=components,
                      sender_id="USEROPENID", origin="qq_official:FriendMessage:USEROPENID")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc.remember_uploadable_message(event))
        pending = list(getattr(svc, "_auto_upload_tasks", []))
        for task in pending:
            loop.run_until_complete(task)
    finally:
        loop.close()

    check("8.1 QQ 官方私聊附件真的上传", len(uploaded) == 1, f"uploaded={uploaded}")
    if uploaded:
        target, name, url = uploaded[0]
        check("8.2 文件名来自组件 name", name == "投稿视频.mp4", f"{name}")
        check("8.3 直链取自组件 url", "file=xyz" in url, f"{url}")
        check("8.4 上传到带日期子目录", target.startswith("/20") and target.count("/") == 1,
              f"target={target}")


def t9_manual_upload_command_qq_official():
    """端到端：「素材 上传」指令在 QQ 官方平台上真的把最近附件上传到 OpenList。"""
    plugin = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
    })
    svc = plugin.upload_service
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

        async def upload_file(self, file_path, target_path, filename):
            return True

    plugin._create_openlist_client = lambda cfg: FakeClient()
    plugin.cache_manager = types.SimpleNamespace(clear_cache=lambda *a, **k: None)

    components = [COMP.Image(file="https://multimedia.nt.qq.com.cn/download?img=9")]
    raw = FakeBotpyObject()
    event = FakeEvent(raw_message=raw, group_id=None, components=components,
                      sender_id="USEROPENID", message_str="素材 上传 我的生日照")

    async def collect():
        return [r async for r in svc.upload_command(event, "我的生日照")]

    loop = asyncio.new_event_loop()
    try:
        # 先让 remember 把附件缓存起来（模拟用户先发图、再打指令）
        loop.run_until_complete(svc.remember_uploadable_message(event))
        # 清掉自动上传任务，只验证手动指令路径
        for t in list(getattr(svc, "_auto_upload_tasks", [])):
            loop.run_until_complete(t)
        uploaded.clear()
        results = loop.run_until_complete(collect())
    finally:
        loop.close()

    check("9.1 「素材 上传」在 QQ 官方平台确实上传", len(uploaded) == 1, f"uploaded={uploaded}")
    if uploaded:
        target, name, url = uploaded[0]
        check("9.2 图片扩展名补全为 .jpg", name.endswith(".jpg"), f"name={name}")
    joined = "\n".join(str(r) for r in results)
    check("9.3 指令返回了结果文本", bool(joined.strip()), f"results={joined[:200]}")


def t10_qq_official_group_file():
    """QQ 官方群聊：File 附件在开关开启时自动转存，图片不自动转存。"""
    plugin = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
        "group_file_auto_upload": True,
        "group_file_upload_path": "/群文件",
    })
    svc = plugin.upload_service
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

    plugin._create_openlist_client = lambda cfg: FakeClient()

    loop = asyncio.new_event_loop()
    try:
        # 群聊里的 File 附件 → 自动转存
        file_comp = COMP.File(name="docs.zip", file="https://multimedia.nt.qq.com.cn/download?fileid=zip",
                              url="https://multimedia.nt.qq.com.cn/download?fileid=zip")
        ev = FakeEvent(raw_message=FakeBotpyObject(), group_id="GROUP_OPENID", components=[file_comp],
                       sender_id="USER_OPENID", origin="qq_official:GroupMessage:GROUP_OPENID")
        loop.run_until_complete(svc.remember_uploadable_message(ev))
        for t in list(svc._auto_upload_tasks):
            loop.run_until_complete(t)

        check("10.1 QQ 官方群聊 File 自动转存到指定目录",
              any(u[0] == "/群文件" for u in uploaded), f"uploaded={uploaded}")
        check("10.2 文件名保留",
              any(u[1] == "docs.zip" for u in uploaded), f"names={[u[1] for u in uploaded]}")

        # 群聊里的图片 → 不自动转存（避免刷屏）
        before = len(uploaded)
        img = COMP.Image(file="https://multimedia.nt.qq.com.cn/download?img=1")
        ev2 = FakeEvent(raw_message=FakeBotpyObject(), group_id="GROUP_OPENID", components=[img],
                        sender_id="USER_OPENID", origin="qq_official:GroupMessage:GROUP_OPENID")
        loop.run_until_complete(svc.remember_uploadable_message(ev2))
        for t in list(svc._auto_upload_tasks):
            loop.run_until_complete(t)
        check("10.3 QQ 官方群聊图片不自动转存", len(uploaded) == before, f"uploaded={uploaded}")
    finally:
        loop.close()

    # 开关关闭时：File 也不自动转存
    plugin2 = FakePlugin(global_cfg={
        "require_user_auth": False,
        "openlist_url": "http://openlist.local:5244",
        "max_upload_size": 100,
        "group_file_auto_upload": False,
    })
    svc2 = plugin2.upload_service
    uploaded2 = []

    class FakeClient2(FakeClient):
        async def upload_url_stream(self, source_url, target_path, filename, file_size=None):
            uploaded2.append((target_path, filename))
            return True

    plugin2._create_openlist_client = lambda cfg: FakeClient2()
    loop = asyncio.new_event_loop()
    try:
        ev3 = FakeEvent(raw_message=FakeBotpyObject(), group_id="GROUP_OPENID",
                        components=[file_comp], sender_id="USER_OPENID",
                        origin="qq_official:GroupMessage:GROUP_OPENID")
        loop.run_until_complete(svc2.remember_uploadable_message(ev3))
        for t in list(svc2._auto_upload_tasks):
            loop.run_until_complete(t)
    finally:
        loop.close()
    check("10.4 开关关闭时 QQ 官方群文件不自动转存", not uploaded2, f"uploaded={uploaded2}")


def t11_webui_toggle_reaches_runtime():
    """WebUI 配置项的开关/目录要能真实影响群文件自动转存行为。"""
    # 直接构造真实插件（用桩件 astrbot），config 模拟 WebUI 传来的 global_settings
    import importlib
    import http.server
    import threading

    main_mod = importlib.import_module(
        f"{os.path.basename(_REPO_ROOT)}.main"
    )

    # 起一个真实 HTTP 服务，提供可被 HEAD/Range 探测大小的文件（真实走 _probe_url_size + 流式上传）
    serve_dir = tempfile.mkdtemp(prefix="ol_serve_")
    with open(os.path.join(serve_dir, "a.txt"), "wb") as fh:
        fh.write(b"x" * 2048)

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=serve_dir, **k)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    real_url = f"http://127.0.0.1:{port}/a.txt"

    async def _send_noop(*a, **k):
        return None

    plugin = main_mod.OpenlistPlugin(
        context=types.SimpleNamespace(send_message=_send_noop),
        config={"global_settings": {
            "default_openlist_url": "http://openlist.local:5244",
            "require_user_auth": False,
            "group_file_auto_upload": True,
            "group_file_upload_path": "/webui群文件",
        }},
    )
    svc = plugin.upload_service

    check("11.1 WebUI 开关被 get_global_config 读到",
          svc._auto_upload_group_file_enabled() is True)

    cfg = plugin.get_global_config()
    check("11.2 WebUI 目录进入全局配置",
          cfg.get("group_file_upload_path") == "/webui群文件", f"cfg={cfg.get('group_file_upload_path')!r}")

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
            uploaded.append((target_path, filename, file_size))
            return True

        async def get_direct_download_link(self, path):
            return None

        async def upload_file(self, *a, **k):
            return True

    plugin._create_openlist_client = lambda cfg: FakeClient()

    file_comp = COMP.File(name="a.txt", file=real_url, url=real_url)
    ev = FakeEvent(raw_message=FakeBotpyObject(), group_id="G1", components=[file_comp],
                   sender_id="U1", origin="qq_official:GroupMessage:G1")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(svc.remember_uploadable_message(ev))
        for t in list(svc._auto_upload_tasks):
            loop.run_until_complete(t)
    finally:
        loop.close()
        httpd.shutdown()

    check("11.3 WebUI 目录真的被用作转存目标",
          any(u[0] == "/webui群文件" for u in uploaded), f"uploaded={uploaded}")
    check("11.4 无大小的 QQ 官方群文件经真实 HEAD 探测后仍能上传",
          any(u[2] == 2048 for u in uploaded), f"uploaded={uploaded}")


if __name__ == "__main__":
    t1_group_upload_notice()
    t2_botpy_object_components()
    t3_qq_official_private()
    t4_group_file_url_no_onebot()
    t5_onebot_regression()
    t6_group_file_disabled_by_default()
    t7_end_to_end_group_file_upload()
    t8_end_to_end_qq_official_private_upload()
    t9_manual_upload_command_qq_official()
    t10_qq_official_group_file()
    t11_webui_toggle_reaches_runtime()
    print(f"\n通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败:", FAIL)
        sys.exit(1)
