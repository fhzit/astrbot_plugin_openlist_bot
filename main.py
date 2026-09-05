import asyncio
import json
import os
import posixpath
import secrets
import string
import time
import uuid
from typing import List, Dict, Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.message_components import File
from astrbot.api import logger
from .lib.client import OpenlistClient
from .lib.config import (
    EXTENSION_CONFIG_KEYS,
    GLOBAL_LEGACY_CONFIG_KEYS,
    WEBUI_CONFIG_MAPPING,
    UserConfigManager,
    GlobalConfigManager,
)
from .lib.cache import CacheManager
from .services import BrowseService, ConfigCommandService, DownloadService, HelpService, PreviewService, UploadService
from .services.account_service import AccountService


class OpenlistPlugin(Star):
    LEGACY_ALLOWED_EXTENSIONS = {
        ".txt", ".pdf", ".doc", ".docx", ".zip", ".rar", ".jpg", ".png", ".gif", ".mp4", ".mp3"
    }

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.user_config_managers = {}
        self.config = config
        self.global_config_manager = GlobalConfigManager("openlist")
        self.cache_manager = CacheManager("openlist")
        self.user_navigation_state = {}
        self.recent_upload_messages = {}
        self.upload_service = UploadService(self)
        self.download_service = DownloadService(self)
        self.browse_service = BrowseService(self)
        self.config_command_service = ConfigCommandService(self)
        self.preview_service = PreviewService(self)
        self.help_service = HelpService(self)
        self.account_service = AccountService(self)

    def get_webui_config(self, key: str, default=None):
        """获取WebUI配置项"""
        if self.config:
            return self.config.get("global_settings", {}).get(key, default)
        return default

    def get_global_config(self) -> Dict:
        """获取整合后的全局配置（WebUI + global_config.json）"""
        # 直接加载本地配置
        config = self.global_config_manager.load_config()

        defaults = self.global_config_manager.default_config
        for webui_key, local_key in WEBUI_CONFIG_MAPPING.items():
            webui_val = self.get_webui_config(webui_key)
            if webui_val is not None:
                current_val = config.get(local_key)
                default_val = defaults.get(local_key, defaults.get(webui_key))
                if current_val in (None, "") or current_val == default_val:
                    config[local_key] = webui_val

        # 兼容旧版 global_config.json 中的 default_* 字段
        for legacy_key, local_key in GLOBAL_LEGACY_CONFIG_KEYS.items():
            if not config.get(local_key) and config.get(legacy_key):
                config[local_key] = config[legacy_key]

        # 统一将扩展名字符串转为列表
        for key in EXTENSION_CONFIG_KEYS:
            if isinstance(config.get(key), str):
                config[key] = [ext.strip().lower() for ext in config[key].split(",") if ext.strip()]
                config[key] = [ext if ext.startswith(".") else f".{ext}" for ext in config[key]]

        return config

    def _get_size_limit_mb(self, user_config: Dict, key: str, default: int) -> int:
        """读取大小限制配置；0 表示不限制。"""
        try:
            value = int(user_config.get(key, default))
        except (TypeError, ValueError):
            logger.warning(f"配置 {key} 的值无效: {user_config.get(key)!r}，已使用默认值 {default}MB")
            return default
        if value < 0:
            logger.warning(f"配置 {key} 的值不能为负数: {value}，已使用默认值 {default}MB")
            return default
        return value

    def _get_cache_duration_seconds(self, user_config: Dict) -> int:
        """读取缓存有效期，单位秒。"""
        try:
            duration = int(user_config.get("cache_duration", 300))
        except (TypeError, ValueError):
            logger.warning(f"配置 cache_duration 的值无效: {user_config.get('cache_duration')!r}，已使用默认值 300 秒")
            return 300
        if duration < 1:
            logger.warning(f"配置 cache_duration 的值过小: {duration}，已使用默认值 300 秒")
            return 300
        return duration

    def _get_positive_int_config(self, user_config: Dict, key: str, default: int, minimum: int = 1) -> int:
        """读取正整数配置。"""
        try:
            value = int(user_config.get(key, default))
        except (TypeError, ValueError):
            logger.warning(f"配置 {key} 的值无效: {user_config.get(key)!r}，已使用默认值 {default}")
            return default
        if value < minimum:
            logger.warning(f"配置 {key} 的值过小: {value}，已使用默认值 {default}")
            return default
        return value

    def _get_bool_config(self, user_config: Dict, key: str, default: bool = False) -> bool:
        """读取布尔配置。"""
        value = user_config.get(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)

    def _get_transfer_config(self, user_config: Dict) -> Dict:
        """读取上传/中转传输调优配置。"""
        mb = 1024 * 1024
        return {
            "upload_chunk_size": self._get_positive_int_config(user_config, "upload_chunk_size_mb", 4) * mb,
            "upload_progress_step": self._get_positive_int_config(user_config, "upload_progress_step_mb", 64) * mb,
            "upstream_connect_timeout": self._get_positive_int_config(user_config, "upstream_connect_timeout", 60),
            "upstream_read_timeout": self._get_positive_int_config(user_config, "upstream_read_timeout", 180),
            "openlist_connect_timeout": self._get_positive_int_config(user_config, "openlist_connect_timeout", 30),
            "openlist_upload_response_timeout": self._get_positive_int_config(user_config, "openlist_upload_response_timeout", 3000),
            "debug_transfer_logging": self._get_bool_config(user_config, "debug_transfer_logging", False),
        }

    def _create_openlist_client(self, user_config: Dict) -> OpenlistClient:
        """基于用户配置创建 OpenList 客户端。"""
        return OpenlistClient(
            user_config["openlist_url"],
            user_config.get("public_openlist_url", ""),
            user_config.get("username", ""),
            user_config.get("password", ""),
            user_config.get("token", ""),
            user_config.get("fixed_base_directory", ""),
            transfer_config=self._get_transfer_config(user_config),
        )

    def _get_retry_config(self, user_config: Dict, prefix: str) -> tuple:
        """读取重试配置；attempts 包含首次尝试。"""
        return (
            self._get_positive_int_config(user_config, f"{prefix}_retry_attempts", 3),
            self._get_positive_int_config(user_config, f"{prefix}_retry_delay", 5, minimum=0),
        )

    def _get_extension_filter(self, user_config: Dict, key: str = "allowed_extensions") -> List[str]:
        """读取扩展名过滤配置；空列表表示不限制。"""
        value = user_config.get(key, [])
        if isinstance(value, str):
            extensions = [ext.strip().lower() for ext in value.split(",") if ext.strip()]
        elif isinstance(value, list):
            extensions = [str(ext).strip().lower() for ext in value if str(ext).strip()]
        else:
            return []
        extensions = [ext if ext.startswith(".") else f".{ext}" for ext in extensions]
        if key == "allowed_extensions" and set(extensions) == self.LEGACY_ALLOWED_EXTENSIONS:
            return []
        return extensions

    def _is_extension_allowed(self, filename: str, user_config: Dict, key: str = "allowed_extensions") -> bool:
        """判断文件扩展名是否通过配置过滤。"""
        allowed_exts = self._get_extension_filter(user_config, key)
        if not allowed_exts:
            return True
        return os.path.splitext((filename or "").lower())[1] in allowed_exts

    def _format_extension_filter(self, user_config: Dict, key: str = "allowed_extensions") -> str:
        allowed_exts = self._get_extension_filter(user_config, key)
        return ", ".join(allowed_exts) if allowed_exts else "不限制"

    def _is_admin_role(self, role) -> bool:
        """兼容 AstrBot/适配器可能返回的数字或字符串群角色。"""
        if role is None:
            return False
        for attr in ("name", "value"):
            attr_value = getattr(role, attr, None)
            if attr_value is not None and attr_value is not role:
                if self._is_admin_role(attr_value):
                    return True
        if isinstance(role, str):
            role_text = role.strip().lower()
            if "." in role_text:
                role_text = role_text.rsplit(".", 1)[-1]
            if role_text in ("owner", "admin", "administrator", "superuser", "super_admin", "root", "群主", "管理员"):
                return True
            if role_text in ("member", "normal", "user", "guest", "成员", "群员", "普通用户"):
                return False
            try:
                return int(role_text) >= 2
            except ValueError:
                return False
        try:
            return int(role) >= 2
        except (TypeError, ValueError):
            return False

    def _read_value(self, obj, key: str, default=None):
        """从对象或映射中读取字段，兼容适配器原始事件对象。"""
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        value = getattr(obj, key, default)
        if value is not default:
            return value
        try:
            return obj[key]
        except Exception:
            return default

    def _get_event_group_id(self, event: AstrMessageEvent):
        """从 AstrBot 事件或平台原始事件中读取群号。"""
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", None)
        if group_id not in (None, ""):
            return group_id

        raw_message = self._read_value(message_obj, "raw_message")
        return self._read_value(raw_message, "group_id")

    def _get_navigation_state_key(self, event: AstrMessageEvent) -> str:
        """按会话隔离导航状态，避免同一用户在不同群/私聊串列表序号。"""
        user_id = event.get_sender_id()
        group_id = self._get_event_group_id(event)
        if group_id not in (None, ""):
            return f"group:{group_id}:user:{user_id}"
        return f"private:user:{user_id}"

    async def _get_group_member_role(self, event: AstrMessageEvent, group_id, user_id=None):
        """通过 OneBot 查询指定用户在目标群的角色。"""
        user_id = user_id or event.get_sender_id()
        try:
            member_info = await event.bot.api.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=True,
            )
        except Exception as e:
            logger.warning(f"查询目标群成员权限失败: group={group_id}, user={user_id}, err={e}")
            return None

        if not isinstance(member_info, dict):
            return None
        return member_info.get("role") or member_info.get("permission")

    async def _has_target_group_permission(self, event: AstrMessageEvent, group_id) -> bool:
        """允许当前群直接操作；跨群/私聊指定群时要求目标群群主或管理员。"""
        current_group_id = self._get_event_group_id(event)
        if current_group_id not in (None, "") and str(current_group_id) == str(group_id):
            return True

        role = await self._get_group_member_role(event, group_id)
        return self._is_admin_role(role)

    async def _deny_if_no_target_group_permission(self, event: AstrMessageEvent, group_id, action_name: str) -> bool:
        """返回 True 表示权限不足并已记录日志。"""
        if await self._has_target_group_permission(event, group_id):
            return False

        logger.warning(
            f"{action_name}目标群权限不足: user={event.get_sender_id()}, "
            f"current_group={self._get_event_group_id(event)}, target_group={group_id}"
        )
        return True

    def _extract_sender_role(self, event: AstrMessageEvent):
        """尽量从 AstrBot 事件和平台原始事件中提取发送者群角色。"""
        candidates = []

        role = getattr(event, "role", None)
        if role is not None:
            candidates.append(role)

        message_obj = getattr(event, "message_obj", None)
        sender = self._read_value(message_obj, "sender")
        for key in ("role", "permission"):
            value = self._read_value(sender, key)
            if value is not None:
                candidates.append(value)

        raw_message = self._read_value(message_obj, "raw_message")
        raw_sender = self._read_value(raw_message, "sender")
        for key in ("role", "permission"):
            value = self._read_value(raw_sender, key)
            if value is not None:
                candidates.append(value)
        raw_role = self._read_value(raw_message, "role")
        if raw_role is not None:
            candidates.append(raw_role)

        for candidate in candidates:
            if candidate not in (None, ""):
                return candidate
        return None

    def _is_event_admin(self, event: AstrMessageEvent) -> bool:
        """判断事件发送者是否为管理员，优先使用 AstrBot 能力，再回退到平台原始角色。"""
        is_admin = getattr(event, "is_admin", None)
        if callable(is_admin):
            try:
                if is_admin():
                    return True
            except Exception as e:
                logger.debug(f"调用 event.is_admin() 失败，继续使用角色字段判断: {e}")

        return self._is_admin_role(self._extract_sender_role(event))

    async def initialize(self):
        """插件初始化"""
        logger.info("Openlist文件管理插件已加载")
        global_cfg = self.get_global_config()
        default_url = global_cfg.get("openlist_url", "")
        require_auth = global_cfg.get("require_user_auth", True)
        if not default_url and not require_auth:
            logger.warning("Openlist URL未配置，请使用 素材 配置 命令配置或在WebUI中配置")
        # 后台补齐白名单用户账户（含 WebUI 新增的高级白名单用户）
        asyncio.create_task(self._reconcile_whitelist_accounts())

    async def _reconcile_whitelist_accounts(self):
        """扫描全部白名单用户（高级+普通），为缺失的 OpenList 账户自动创建。

        在插件加载/WebUI 热重载时执行，保证 WebUI 新增的高级白名单用户也有账户。
        """
        try:
            global_cfg = self.get_global_config()
        except Exception as e:
            logger.error(f"账户对账失败(读取配置): {e}", exc_info=True)
            return
        advanced = self._get_submit_whitelist(global_cfg)
        normal = self._get_submit_whitelist_normal()
        all_whitelist = list(dict.fromkeys([str(q).strip() for q in (advanced + normal) if str(q).strip()]))
        for qq in all_whitelist:
            try:
                if self.account_service._stored_account(qq):
                    continue
                acct = await self.account_service.create_account(qq)
                if acct.get("ok"):
                    logger.info(f"账户对账：已自动创建/纳入 QQ {qq} 的 OpenList 账户")
                else:
                    logger.warning(f"账户对账：QQ {qq} 建号失败 - {acct.get('message')}")
            except Exception as e:
                logger.error(f"账户对账：QQ {qq} 异常: {e}", exc_info=True)

    def get_user_config_manager(self, user_id: str) -> UserConfigManager:
        """获取用户配置管理器"""
        if user_id not in self.user_config_managers:
            self.user_config_managers[user_id] = UserConfigManager("openlist", user_id)
        return self.user_config_managers[user_id]

    def get_user_config(self, user_id: str) -> Dict:
        """获取用户配置"""
        global_cfg = self.get_global_config()
        if not global_cfg.get("require_user_auth", True):
            return global_cfg

        user_config = self.get_user_config_manager(user_id).load_config()

        # 简单的合并：用户配置优先，如果用户配置为空则使用全局配置
        final_cfg = global_cfg.copy()
        for k, v in user_config.items():
            default_val = self.get_user_config_manager(user_id).default_config.get(k)
            is_default_value = v == default_val
            if k == "allowed_extensions":
                if isinstance(v, str):
                    normalized_exts = [ext.strip().lower() for ext in v.split(",") if ext.strip()]
                elif isinstance(v, list):
                    normalized_exts = [str(ext).strip().lower() for ext in v if str(ext).strip()]
                else:
                    normalized_exts = []
                normalized_exts = [ext if ext.startswith(".") else f".{ext}" for ext in normalized_exts]
                if set(normalized_exts) == self.LEGACY_ALLOWED_EXTENSIONS:
                    is_default_value = True
            # 只要用户设置了非默认值，就覆盖全局；允许 0/False/[] 这类有效配置值。
            if not is_default_value:
                final_cfg[k] = v

        return final_cfg

    def _validate_config(self, user_config: Dict) -> bool:
        """验证配置是否有效"""
        return bool(user_config.get("openlist_url"))

    def get_submit_root(self, user_config: Dict) -> str:
        """返回投稿模式根路径（已归一化），未开启投稿隔离时返回空字符串。"""
        root = (user_config.get("default_submit_path") or "").strip()
        return self._normalize_openlist_path(root) if root else ""

    def is_submit_mode(self, user_config: Dict) -> bool:
        """是否开启投稿模式（投稿隔离）。"""
        return bool(self.get_submit_root(user_config))

    def get_user_submit_dir(self, submit_root: str, sender_id) -> str:
        """返回投稿隔离下某用户的个人目录：<root>/<QQ号>。"""
        return self._normalize_openlist_path(f"{submit_root.rstrip('/')}/{sender_id}")

    def _parse_sender_id(self, session_key: str) -> str:
        """从会话 key（nav_key）中解析真实发送者 QQ。格式：
        group:<gid>:user:<uid>  或  private:user:<uid>
        """
        if ":user:" in session_key:
            return session_key.split(":user:", 1)[1]
        return session_key

    def _parse_q_whitelist(self, raw_value) -> List[str]:
        """把逗号分隔的 QQ 号字符串解析为列表。"""
        raw = (raw_value or "").strip()
        if not raw:
            return []
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                ids.append(part)
        return ids

    def _get_submit_whitelist(self, global_cfg: Dict) -> List[str]:
        """解析【高级白名单】Q 号列表（来自 WebUI submit_whitelist，逗号分隔）。"""
        return self._parse_q_whitelist(global_cfg.get("submit_whitelist"))

    def _get_submit_whitelist_normal(self) -> List[str]:
        """解析【普通白名单】Q 号列表（来自本地 global_config.json 的 submit_whitelist_normal）。"""
        try:
            raw = self.global_config_manager.load_config().get("submit_whitelist_normal")
        except Exception:
            raw = ""
        return self._parse_q_whitelist(raw)

    def is_whitelisted_user(self, user_id) -> bool:
        """判断某用户是否为投稿白名单用户（普通 或 高级 任一档）。"""
        uid = str(user_id).strip()
        if not uid:
            return False
        try:
            global_cfg = self.get_global_config()
        except Exception:
            return False
        return uid in self._get_submit_whitelist(global_cfg) or uid in self._get_submit_whitelist_normal()

    def is_advanced_whitelisted_user(self, user_id) -> bool:
        """判断某用户是否为【高级白名单】用户（仅高级可执行 白名单 增删指令）。"""
        uid = str(user_id).strip()
        if not uid:
            return False
        try:
            global_cfg = self.get_global_config()
        except Exception:
            return False
        return uid in self._get_submit_whitelist(global_cfg)

    def _submit_anchor_dir(self, submit_root: str, sender_id: str) -> str:
        """投稿模式下用户的访问锚点目录：
        白名单用户锚定到投稿根目录（可浏览/管理所有用户的文件夹），
        非白名单用户锚定到自己的个人目录。"""
        if self.is_whitelisted_user(sender_id):
            return self._normalize_openlist_path(submit_root)
        return self.get_user_submit_dir(submit_root, sender_id)

    def _clamp_to_user_submit_dir(self, resolved_path: str, submit_root: str, sender_id: str) -> str:
        """把解析出的路径限制到投稿用户的允许范围内；越权路径一律拉回。
        白名单用户允许投稿根目录及以下，非白名单用户仅允许自己的个人目录内。"""
        anchor = self._submit_anchor_dir(submit_root, sender_id)
        p = self._normalize_openlist_path(resolved_path)
        alist = anchor.rstrip("/")
        if p.startswith(alist + "/") or p == alist:
            return p
        # 回到根、越权到其他用户/根目录下 → 一律回锚点目录
        return anchor

    def _submit_deny_if_applicable(self, event) -> Optional[str]:
        """投稿模式下屏蔽越权指令（搜索、新建）；未开启投稿模式返回 None。
        删除指令由 remove_command 单独按白名单判断。"""
        try:
            user_config = self.get_user_config(event.get_sender_id())
        except Exception:
            user_config = {}
        if not self.is_submit_mode(user_config):
            return None
        return (
            "🔒 投稿模式已开启：在投稿模式下，搜索、新建目录等指令不可用。"
        )

    async def _ensure_user_folder_for_event(self, event, user_config: Dict) -> str:
        """确保投稿用户个人目录存在（自动新建 <root>/<QQ号>），返回个人目录。投稿未开启时返回 ''。"""
        submit_root = self.get_submit_root(user_config)
        if not submit_root:
            return ""
        user_dir = self.get_user_submit_dir(submit_root, event.get_sender_id())
        try:
            async with self._create_openlist_client(user_config) as client:
                await client.ensure_dir(user_dir)
        except Exception as e:
            logger.warning(f"投稿目录自动创建失败: {user_dir}, err={e}")
        return user_dir

    def _get_user_navigation_state(self, user_id: str) -> Dict:
        """获取用户导航状态"""
        if user_id not in self.user_navigation_state:
            self.user_navigation_state[user_id] = {
                "current_path": "/",
                "items": [],
                "parent_paths": [],
                "current_page": 1,
                "_session_key": user_id,
            }
        return self.user_navigation_state[user_id]

    def _update_user_navigation_state(self, user_id: str, path: str, items: List[Dict]):
        """更新用户导航状态"""
        nav_state = self._get_user_navigation_state(user_id)
        if path != nav_state["current_path"]:
            if self._is_forward_navigation(nav_state["current_path"], path):
                nav_state["parent_paths"].append(nav_state["current_path"])
            nav_state["current_path"] = path
            nav_state["current_page"] = 1
        nav_state["items"] = items

    def _is_forward_navigation(self, current_path: str, new_path: str) -> bool:
        """判断是否是前进导航"""
        current = current_path.rstrip("/")
        new = new_path.rstrip("/")
        return new.startswith(current + "/") if current != "/" else new.startswith("/")

    def _get_item_by_number(self, user_id: str, number: int) -> Optional[Dict]:
        """根据序号获取文件或目录项"""
        nav_state = self._get_user_navigation_state(user_id)
        items = nav_state.get("items")
        if items and 1 <= number <= len(items):
            return items[number - 1]
        return None

    def _normalize_openlist_path(self, path: str) -> str:
        """标准化 OpenList 路径，统一为以 / 开头的绝对路径。"""
        normalized = (path or "").strip().replace("\\", "/")
        if not normalized:
            return "/"
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        normalized = posixpath.normpath(normalized)
        if normalized in ("", "."):
            return "/"
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        return normalized

    def _resolve_target_path(self, user_id: str, path: str, default_to_current: bool = True) -> str:
        """将目标路径解析为 OpenList 绝对路径，支持当前目录相对路径。
        投稿模式下自动限定在用户个人投稿目录内。"""
        raw_path = (path or "").strip()
        nav_state = self._get_user_navigation_state(user_id)
        current_path = nav_state["current_path"]
        if not isinstance(current_path, str) or not current_path.startswith("/"):
            current_path = "/"

        resolved = ""
        if not raw_path:
            if default_to_current:
                resolved = self._normalize_openlist_path(current_path)
            else:
                resolved = "/"
        elif raw_path.startswith("/"):
            resolved = self._normalize_openlist_path(raw_path)
        else:
            current_path = self._normalize_openlist_path(current_path)
            resolved = self._normalize_openlist_path(f"{current_path.rstrip('/')}/{raw_path}")

        # 投稿模式：把解析结果限定到用户个人投稿目录
        submit_root, sender_id = self._submit_context(nav_state)
        if submit_root:
            return self._clamp_to_user_submit_dir(resolved, submit_root, sender_id)
        return resolved

    def _submit_context(self, nav_state: Dict):
        """根据导航状态推导 (submit_root, sender_id)；未开启投稿隔离返回 ("", sender_id)。"""
        try:
            sender_id = self._parse_sender_id(nav_state.get("_session_key", ""))
        except Exception:
            sender_id = ""
        try:
            global_cfg = self.get_global_config()
            submit_root = self.get_submit_root(global_cfg)
        except Exception as e:
            logger.debug(f"读取投稿配置失败: {e}")
            submit_root = ""
        return submit_root, sender_id

    def _resolve_path_candidates(self, user_id: str, path: str, default_to_current: bool = True) -> List[str]:
        """生成候选路径: 先当前目录相对路径，再尝试根目录路径（用于兼容旧用法）。
        投稿模式下统一限定在用户个人投稿目录。"""
        raw_path = (path or "").strip()
        primary_path = self._resolve_target_path(user_id, raw_path, default_to_current=default_to_current)
        candidates = [primary_path]
        if raw_path and not raw_path.startswith("/"):
            root_path = self._normalize_openlist_path(raw_path)
            if root_path not in candidates:
                candidates.append(root_path)

        # 投稿模式下，候选路径全部拉回用户个人投稿目录，避免越权
        nav_state = self._get_user_navigation_state(user_id)
        submit_root, sender_id = self._submit_context(nav_state)
        if submit_root:
            candidates = [self._clamp_to_user_submit_dir(c, submit_root, sender_id) for c in candidates]
            # 去重保序
            seen = set()
            dedup = []
            for c in candidates:
                if c not in seen:
                    seen.add(c)
                    dedup.append(c)
            candidates = dedup
        return candidates

    def _strip_fixed_base_directory(self, path: str, user_config: Dict) -> str:
        """从 OpenList 返回路径中剥离下载链接前缀，得到用户视角路径。"""
        path = self._normalize_openlist_path(path)
        fixed_base_dir = self._normalize_openlist_path(user_config.get("fixed_base_directory", ""))
        if fixed_base_dir != "/" and (path == fixed_base_dir or path.startswith(fixed_base_dir + "/")):
            path = path[len(fixed_base_dir):]
            if not path:
                return "/"
            if not path.startswith("/"):
                path = "/" + path
        return self._normalize_openlist_path(path)

    def _get_item_full_path(self, user_id: str, item: Dict, user_config: Dict) -> str:
        """根据列表项生成 OpenList 绝对路径，兼容普通列表和搜索结果。
        投稿模式下限定在用户个人投稿目录内。"""
        item_name = item.get("name", "")
        parent_path = item.get("parent")
        if parent_path:
            parent_path = self._strip_fixed_base_directory(parent_path, user_config)
            resolved = self._normalize_openlist_path(f"{parent_path.rstrip('/')}/{item_name}")
        else:
            current_path = self._get_user_navigation_state(user_id).get("current_path", "/")
            if not isinstance(current_path, str) or not current_path.startswith("/"):
                current_path = "/"
            resolved = self._normalize_openlist_path(f"{current_path.rstrip('/')}/{item_name}")

        nav_state = self._get_user_navigation_state(user_id)
        submit_root, sender_id = self._submit_context(nav_state)
        if submit_root:
            return self._clamp_to_user_submit_dir(resolved, submit_root, sender_id)
        return resolved

    def _format_file_size(self, size: int) -> str:
        """格式化文件大小"""
        if size < 1024: return f"{size}B"
        elif size < 1024 * 1024: return f"{size / 1024:.1f}KB"
        elif size < 1024 * 1024 * 1024: return f"{size / (1024 * 1024):.1f}MB"
        else: return f"{size / (1024 * 1024 * 1024):.1f}GB"

    def _get_wake_prefixes(self) -> List[str]:
        """读取 AstrBot 唤醒前缀，用于识别根指令时剥离外层唤醒词。"""
        try:
            get_config = getattr(self.context, "get_config", None)
            core_config = get_config() if callable(get_config) else None
        except Exception as e:
            logger.debug(f"读取 AstrBot 唤醒前缀失败: {e}")
            return []

        if not core_config:
            return []
        if isinstance(core_config, dict):
            prefixes = core_config.get("wake_prefix", [])
        else:
            prefixes = getattr(core_config, "wake_prefix", [])
            if prefixes in (None, []):
                try:
                    prefixes = core_config.get("wake_prefix", [])
                except Exception:
                    prefixes = []

        if isinstance(prefixes, str):
            prefixes = [prefixes]
        elif not isinstance(prefixes, list):
            return []
        return sorted((str(prefix) for prefix in prefixes if str(prefix)), key=len, reverse=True)

    def _strip_wake_prefix(self, message: str) -> str:
        """从消息文本中剥离 AstrBot 配置的唤醒前缀，保留插件指令词。"""
        for prefix in self._get_wake_prefixes():
            if message.startswith(prefix):
                return message[len(prefix):].strip()
        return message

    def _format_usage_tip(self, title: str, usage: str, examples: List[str] = None, note: str = "") -> str:
        """生成简短、可读的命令用法提示。"""
        lines = [f"❌ {title}", "", f"用法：{usage}"]
        if examples:
            lines.append("示例：")
            lines.extend(f"  {example}" for example in examples)
        if note:
            lines.extend(["", f"提示：{note}"])
        return "\n".join(lines)

    def _format_config_actions_tip(self, title: str = "配置指令用法错误") -> str:
        """生成配置命令操作提示。"""
        return self._format_usage_tip(
            title,
            "素材 配置 <查看|向导|设置|测试|清缓存>",
            [
                "素材 配置 查看",
                "素材 配置 向导",
                "素材 配置 设置 openlist_url http://127.0.0.1:5244",
                "素材 配置 测试",
            ],
            "设置 用于修改配置项；查看 用于查看当前配置。",
        )

    def _format_upload_usage_tip(self, title: str = "上传指令用法错误") -> str:
        """生成最近附件上传命令提示。"""
        return self._format_usage_tip(
            title,
            "先发送图片、视频或文件，再在 5 分钟内发送 素材 上传 [OpenList目标目录]",
            [
                "素材 上传",
                "素材 上传 /movies",
                "素材 上传 clips",
            ],
            "只会使用同一会话、同一发送者最近 5 分钟内的最近一条附件消息。",
        )

    def _sanitize_filename(self, filename: str, fallback: str = "file") -> str:
        """生成可用于临时附件名的文件名片段。"""
        safe_name = "".join(c for c in (filename or "") if c.isalnum() or c in "._- ").strip(" .")
        return (safe_name[:100] or fallback)

    def _unique_suffix(self) -> str:
        """生成临时文件名后缀，避免同一秒内并发请求撞名。"""
        return f"{time.time_ns()}_{uuid.uuid4().hex[:12]}"

    async def _cleanup_temp_file(self, file_path: str, delay: int = 10):
        """延迟清理已发送的临时文件。"""
        await asyncio.sleep(delay)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError as e:
            logger.debug(f"清理临时文件失败: {file_path}, err={e}")

    def _normalize_download_headers(self, headers: Dict) -> Dict[str, str]:
        """将 OpenList link.header 转为 aiohttp 可用的单值请求头。"""
        normalized = {}
        if not isinstance(headers, dict):
            return normalized
        for key, value in headers.items():
            if value is None:
                continue
            if isinstance(value, list):
                values = [str(v) for v in value if v is not None]
                if not values:
                    continue
                normalized[key] = "; ".join(values) if key.lower() == "cookie" else ",".join(values)
            else:
                normalized[key] = str(value)
        return normalized

    async def _send_download_link_txt(
        self,
        event: AstrMessageEvent,
        file_name: str,
        file_size: int,
        file_path: str,
        download_url: str,
    ):
        """将下载链接写入 txt 附件发送，避免长文本被平台转为图片。"""
        links_dir = os.path.join(StarTools.get_data_dir("openlist"), "links")
        os.makedirs(links_dir, exist_ok=True)
        safe_base = self._sanitize_filename(file_name, "download")
        attachment_name = f"{safe_base}_download_link.txt"
        temp_file_path = os.path.join(
            links_dir,
            f"{event.get_sender_id()}_{self._unique_suffix()}_{attachment_name}",
        )
        content = (
            "OpenList 下载链接\n\n"
            f"文件: {file_name}\n"
            f"路径: {file_path}\n"
            f"大小: {self._format_file_size(file_size)}\n"
            f"链接: {download_url}\n"
        )
        with open(temp_file_path, "w", encoding="utf-8") as f:
            f.write(content)

        yield event.plain_result(f"✅ 已获取下载链接，正在作为 txt 文件发送: {file_name}")
        yield event.chain_result([File(name=attachment_name, file=temp_file_path)])
        asyncio.create_task(self._cleanup_temp_file(temp_file_path))

    def _format_file_list(self, files: List[Dict], current_path: str, user_config: Dict, user_id: str = None) -> str:
        """格式化文件列表或搜索结果"""
        is_search_result = current_path.startswith("🔍 搜索")
        title = f"📁 {current_path}" if not is_search_result else current_path

        if not files: return f"{title}\n\n❌ 列表为空"

        nav_state = self._get_user_navigation_state(user_id)
        current_page = nav_state.get("current_page", 1)
        max_files_per_page = user_config.get("max_display_files", 20)
        total_items = len(files)
        total_pages = (total_items + max_files_per_page - 1) // max_files_per_page
        start_index = (current_page - 1) * max_files_per_page
        end_index = start_index + max_files_per_page
        items_to_display = files[start_index:end_index]

        result = f"{title}\n\n"

        dirs_count = 0
        files_only_count = 0
        if not is_search_result:
            dirs_count = len([f for f in files if f.get("is_dir", False)])
            files_only_count = total_items - dirs_count

        for i, item in enumerate(items_to_display, start=start_index + 1):
            name = item.get("name", "")
            size = item.get("size", 0)
            modified = item.get("modified", "")
            is_dir = item.get("is_dir", False)

            if is_dir: icon = "📂"
            else:
                ext = os.path.splitext(name)[1].lower()
                if ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp"]: icon = "🖼️"
                elif ext in [".mp4", ".avi", ".mkv", ".mov"]: icon = "🎬"
                elif ext in [".mp3", ".wav", ".flac", ".aac"]: icon = "🎵"
                elif ext in [".pdf"]: icon = "📄"
                elif ext in [".doc", ".docx"]: icon = "📝"
                elif ext in [".zip", ".rar", ".7z"]: icon = "📦"
                else: icon = "📄"

            result += f"{i:2d}. {icon} {name}{'/' if is_dir else ''}\n"

            extra_info = []
            if is_search_result:
                parent = item.get("parent", "")
                if parent:
                    parent = self._strip_fixed_base_directory(parent, user_config)
                    extra_info.append(f"📍 {parent}")
                if not is_dir or size > 0:
                    extra_info.append(f"💾 {self._format_file_size(size)}")
            else:
                if not is_dir or size > 0:
                    extra_info.append(f"💾 {self._format_file_size(size)}")

                modified_date_part = modified.split('T')[0] if modified else ''
                if modified_date_part:
                    extra_info.append(f"📅 {modified_date_part}")

            if extra_info:
                result += f"      {' | '.join(extra_info)}\n"

        result += f"\n📄 第 {current_page} / {total_pages} 页"
        if is_search_result:
            result += f" | 📊 总计: {total_items} 个结果"
        else:
            dirs_count = len([f for f in files if f.get("is_dir", False)])
            files_only_count = total_items - dirs_count
            result += f" | 📊 总计: {dirs_count} 个文件夹, {files_only_count} 个文件"

        result += f"\n\n💡 快速导航:"
        result += f"\n\n   • 素材 列表 序号 - 进入目录/获取链接"
        result += f"\n\n   • 素材 下载 序号 - 下载并发送文件"
        if not is_search_result:
             result += f"\n\n   • 素材 上一级 - 返回上级目录"
        if total_pages > 1:
            result += f"\n   • 素材 上一页 - ⬅️ 上一页"
            result += f"\n   • 素材 下一页 - ➡️ 下一页"
        return result

    @filter.event_message_type(filter.EventMessageType.ALL, priority=3)
    async def remember_recent_upload_message(self, event: AstrMessageEvent):
        """记录最近附件消息，供 素材 上传 使用。"""
        await self.upload_service.remember_uploadable_message(event)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100000)
    async def handle_openlist_root_help(self, event: AstrMessageEvent):
        """拦截 素材 根指令，避免展示框架生成的参数树。"""
        message = self._strip_wake_prefix((event.message_str or "").strip())
        if message not in ("素材",):
            return
        event.stop_event()
        async for result in self.help_service.help_command(event):
            yield result

    @filter.command_group("素材")
    def openlist_group(self):
        """Openlist文件管理命令组"""
        pass

    @openlist_group.command("配置", alias=["设置"])
    async def config_command(self, event: AstrMessageEvent, action: str = "查看", key: str = "", value: str = ""):
        """配置连接与插件参数。
        示例：
          素材 配置
          素材 配置 设置 openlist_url http://127.0.0.1:5244
        """
        async for result in self.config_command_service.config_command(event, action, key, value):
            yield result

    @openlist_group.command("列表", alias=["直链"])
    async def list_files(self, event: AstrMessageEvent, path: str = ""):
        """列出目录或获取文件链接。
        示例：
          素材 列表 /movies
          素材 列表 2
        """
        async for result in self.browse_service.list_files(event, path):
            yield result

    @openlist_group.command("下一页")
    async def next_page(self, event: AstrMessageEvent):
        """查看当前列表下一页。示例：素材 下一页"""
        async for result in self.browse_service.next_page(event):
            yield result

    @openlist_group.command("上一页")
    async def prev_page(self, event: AstrMessageEvent):
        """查看当前列表上一页。示例：素材 上一页"""
        async for result in self.browse_service.prev_page(event):
            yield result

    @openlist_group.command("搜索")
    async def search_command(self, event: AstrMessageEvent, keyword: str = "", path: str = ""):
        """搜索文件。
        示例：
          素材 搜索 年度报告
          素材 搜索 年度报告 /documents
        """
        denied = self._submit_deny_if_applicable(event)
        if denied is not None:
            yield event.plain_result(denied)
            return
        async for result in self.browse_service.search_files(event, keyword, path):
            yield result

    @openlist_group.command("信息")
    async def file_info(self, event: AstrMessageEvent, path: str = ""):
        """查看文件或目录信息。示例：素材 信息 /docs/report.pdf"""
        async for result in self.browse_service.file_info(event, path):
            yield result

    @openlist_group.command("重置密码")
    async def reset_password_command(self, event: AstrMessageEvent):
        """重置本人 OpenList 账户密码并展示新密码。

        示例：素材 重置密码
        """
        user_id = event.get_sender_id()
        # 非白名单用户无响应
        if not self.is_whitelisted_user(user_id):
            return
        qq = str(user_id).strip()
        acct = await self.account_service.reset_password(qq)
        if not acct.get("ok"):
            yield event.plain_result(acct.get("message"))
            return
        base_path = acct.get("base_path")
        text = (
            f"{acct.get('message')}\n"
            f"🔑 OpenList 账户：\n"
            f"  地址：{self._get_public_site_url()}\n"
            f"  用户名：{acct.get('username')}\n"
            f"  新密码：`{acct.get('password')}`\n"
        )
        if base_path and base_path != "/":
            text += f"  基础路径：{base_path}\n"
        text += "💡 请妥善保管新密码，可随时再次重置。"
        yield event.plain_result(text)

    @openlist_group.command("网站")
    async def website_command(self, event: AstrMessageEvent):
        """跳转云盘网站。显示 OpenList 云盘地址（优先对外地址）与账户用户名。

        示例：素材 网站
        """
        user_id = event.get_sender_id()
        # 非白名单用户无响应
        if not self.is_whitelisted_user(user_id):
            return
        user_config = self.get_user_config(user_id)
        site_url = (user_config.get("public_openlist_url") or "").strip()
        if not site_url:
            site_url = (user_config.get("openlist_url") or "").strip()
        if not site_url:
            yield event.plain_result("❓ 尚未配置云盘网站地址。\n💡 管理员可在后台插件配置中填写 openlist_url / public_openlist_url。")
            return
        username = str(user_id).strip()
        yield event.plain_result(
            f"☁️ 云盘网站：\n{site_url}\n\n"
            f"👤 用户名：{username}\n\n"
            f"💡 点击上方链接即可跳转打开云盘。"
        )

    def _get_public_site_url(self) -> str:
        """获取向用户展示用的 OpenList 访问地址（优先对外地址）。"""
        try:
            user_config = self.get_global_config()
        except Exception:
            return ""
        return (user_config.get("public_openlist_url") or user_config.get("openlist_url") or "").strip()

    @openlist_group.command("下载")
    async def get_download_link(self, event: AstrMessageEvent, path: str = ""):
        """下载并发送文件。
        示例：
          素材 下载 3
          素材 下载 /docs/report.pdf
        """
        async for result in self.browse_service.get_download_link(event, path):
            yield result

    @openlist_group.command("上一级", alias=["返回"])
    async def quit_navigation(self, event: AstrMessageEvent):
        """返回上级目录。示例：素材 上一级"""
        async for result in self.browse_service.quit_navigation(event):
            yield result

    @openlist_group.command("上传")
    async def upload_command(self, event: AstrMessageEvent, target: str = ""):
        """上传最近附件消息。

        示例：
         先发送图片、视频或文件
         素材 上传 /movies
         素材 上传 周日整理的资料
        """
        user_id = event.get_sender_id()
        try:
            user_config = self.get_user_config(user_id)
        except Exception:
            user_config = {}
        # 投稿模式下：仅白名单用户可手动执行「素材 上传」；素材提交走私聊自动上传
        if self.is_submit_mode(user_config) and not self.is_whitelisted_user(user_id):
            yield event.plain_result("🔒 投稿模式下，请直接私聊发送素材给机器人，系统会自动上传到你的投稿文件夹。")
            return
        # 合并 AstrBot 解析的首个参数与指令后的完整文字，支持多词自定义说明
        target = (target or "").strip()
        raw_text = self._strip_upload_command_prefix(event)
        if raw_text:
            target = raw_text
        async for result in self.upload_service.upload_command(event, target):
            yield result

    def _strip_upload_command_prefix(self, event: AstrMessageEvent) -> str:
        """从事件消息中剥离 '素材 上传' 前缀，返回后面的完整正文。

        例如：
          素材 上传 /movies        -> "/movies"
          素材 上传 周日整理资料     -> "周日整理资料"
          素材 上传                 -> ""
        """
        message = self._strip_wake_prefix((event.message_str or "").strip())
        # 先剥离命令组关键词 素材
        group_word = "素材"
        lowered = message.lower()
        if lowered == group_word.lower():
            message = ""
        elif lowered.startswith(group_word.lower() + " "):
            message = message[len(group_word):].strip()
        # 再剥离子命令 上传
        for head in ("上传",):
            lowered = message.lower()
            if lowered == head.lower():
                return ""
            if lowered.startswith(head.lower() + " "):
                return message[len(head):].strip()
        return ""

    @openlist_group.command("预览")
    async def preview_command(self, event: AstrMessageEvent, path: str = ""):
        """预览文本或压缩包。
        示例：
          素材 预览 1
          素材 预览 /data/config.txt
        """
        async for result in self.preview_service.preview_command(event, path):
            yield result

    @openlist_group.command("删除")
    async def remove_command(self, event: AstrMessageEvent, path: str = ""):
        """删除文件或文件夹。
        示例：
          素材 删除 4
          素材 删除 /tmp/stale.txt
        """
        user_id = event.get_sender_id()
        # 投稿模式下：仅白名单用户可删除
        try:
            user_config = self.get_user_config(user_id)
        except Exception:
            user_config = {}
        if self.is_submit_mode(user_config) and not self.is_whitelisted_user(user_id):
            yield event.plain_result("🔒 投稿模式下仅白名单用户可执行删除操作。")
            return
        async for result in self.browse_service.remove_command(event, path):
            yield result

    @openlist_group.command("新建")
    async def mkdir_command(self, event: AstrMessageEvent, name: str = ""):
        """创建文件夹。
        示例：
          素材 新建 new_folder
          素材 新建 /data/new_dir
        """
        denied = self._submit_deny_if_applicable(event)
        if denied is not None:
            yield event.plain_result(denied)
            return
        async for result in self.browse_service.mkdir_command(event, name):
            yield result

    @openlist_group.command("白名单")
    async def whitelist_command(self, event: AstrMessageEvent, action: str = "", qq: str = ""):
        """管理【普通白名单】（仅高级白名单用户可执行）。

        示例：
          素材 白名单 增加 10001
          素材 白名单 增加10001
          素材 白名单 删除 10001
          素材 白名单 删除10001
          素材 白名单 查看
        """
        user_id = event.get_sender_id()
        # 1) 仅高级白名单用户可执行
        if not self.is_advanced_whitelisted_user(user_id):
            yield event.plain_result("🔒 白名单增删指令仅限【高级白名单】用户使用。\n💡 普通白名单与普通用户在投稿权限上相同，仅高级白名单可管理白名单。")
            return

        # 2) 解析动作与 QQ（兼容 “增加10001” 无空格 与 “增加 10001” 带空格）
        action = (action or "").strip()
        qq = (qq or "").strip()
        if not action:
            # 可能是 "增加10001" 被当作单个参数落入 qq
            if qq:
                action, qq = self._split_whitelist_action(qq)
        else:
            # 处理 “增加10001” 形式：动作后直接跟数字
            action, tail = self._split_whitelist_action(action)
            if not qq and tail:
                qq = tail

        if action in ("查看", "list", "显示"):
            yield event.plain_result(self._whitelist_status_text())
            return

        if action not in ("增加", "添加", "add", "删除", "移除", "del", "remove"):
            yield event.plain_result(self._whitelist_usage_tip())
            return

        qq = qq.strip()
        if not qq or not qq.isdigit():
            yield event.plain_result(self._whitelist_usage_tip())
            return

        is_add = action in ("增加", "添加", "add")
        normal = self._get_submit_whitelist_normal()
        existed = qq in normal
        if is_add:
            if existed:
                yield event.plain_result(f"ℹ️ QQ {qq} 已在普通白名单中，无需重复添加。")
                return
            # 先建 OpenList 账户，成功后再写入白名单，避免建号失败却进入白名单
            acct = await self.account_service.create_account(qq)
            if not acct.get("ok"):
                yield event.plain_result(f"{acct.get('message')}\n❌ 白名单未变更，请解决 OpenList 账户问题后重试。")
                return
            normal.append(qq)
            self._set_submit_whitelist_normal(normal)
            base_path = acct.get("base_path")
            resp = (
                f"✅ 已将 QQ {qq} 加入普通白名单。\n"
                f"🔑 OpenList 账户：\n"
                f"  地址：{self._get_public_site_url()}\n"
                f"  用户名：{acct.get('username')}\n"
                f"  密码：`{acct.get('password')}`\n"
            )
            if base_path and base_path != "/":
                resp += f"  基础路径：{base_path}\n"
            resp += f"📋 当前普通白名单: {self._format_q_list(normal)}"
            yield event.plain_result(resp)
        else:
            if not existed:
                yield event.plain_result(f"ℹ️ QQ {qq} 不在普通白名单中，无需删除。")
                return
            # 先删除 OpenList 账户（失败则中止，保留白名单），成功后再移除白名单
            acct = await self.account_service.delete_account(qq)
            if not acct.get("ok"):
                yield event.plain_result(f"{acct.get('message')}\n❌ 白名单未变更。")
                return
            normal.remove(qq)
            self._set_submit_whitelist_normal(normal)
            yield event.plain_result(f"{acct.get('message')}\n📋 当前普通白名单: {self._format_q_list(normal)}")

    def _split_whitelist_action(self, raw: str):
        """从形如 “增加10001”/“删除10001” 中拆出动作与 QQ；否则原样返回。"""
        raw = (raw or "").strip()
        for keyword in ("增加", "添加", "add", "删除", "移除", "del", "remove"):
            if raw.lower().startswith(keyword.lower()):
                tail = raw[len(keyword):].strip()
                return keyword, tail
        return raw, ""

    def _whitelist_usage_tip(self):
        return (
            "📋 白名单管理指令用法：\n"
            "  素材 白名单 增加 10001     （加入普通白名单）\n"
            "  素材 白名单 删除 10001     （移除普通白名单）\n"
            "  素材 白名单 查看            （查看当前白名单）\n"
            "💡 仅【高级白名单】用户可执行本指令；普通白名单通过本指令管理，高级白名单只能在 WebUI 设置。"
        )

    def _whitelist_status_text(self):
        try:
            global_cfg = self.get_global_config()
        except Exception:
            global_cfg = {}
        advanced = self._get_submit_whitelist(global_cfg)
        normal = self._get_submit_whitelist_normal()
        return (
            "📋 白名单状态：\n"
            f"🔺 高级白名单(仅WebUI): {self._format_q_list(advanced)}\n"
            f"🔹 普通白名单(指令管理): {self._format_q_list(normal)}"
        )

    def _set_submit_whitelist_normal(self, qq_list):
        """把普通白名单写回本地 global_config.json。"""
        raw = ",".join(qq_list)
        try:
            cfg = self.global_config_manager.load_config()
            cfg["submit_whitelist_normal"] = raw
            self.global_config_manager.save_config(cfg)
        except Exception as e:
            logger.error(f"保存普通白名单失败: {e}", exc_info=True)

    def _format_q_list(self, qq_list):
        return ", ".join(qq_list) if qq_list else "（空）"

    @openlist_group.command("帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示完整帮助。示例：素材 帮助"""
        async for result in self.help_service.help_command(event):
            yield result

    async def terminate(self):
        """插件卸载时执行的清理操作"""
        logger.info("OpenList助手已卸载")
