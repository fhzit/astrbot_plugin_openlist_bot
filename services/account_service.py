import json
import os
import secrets
import string
from typing import Dict, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .base import PluginService

# 写内容(创建/上传/修改) + 重命名 + 移动 + 复制 + 删除 = 8|16|32|64|128 = 248
SUBMIT_ACCOUNT_PERMISSION = 8 | 16 | 32 | 64 | 128


class AccountService(PluginService):
    """OpenList 账户管理服务：为提交白名单用户自动开通/停用独立 OpenList 账户。

    依赖插件全局配置里的管理员凭据（default_username / default_password /
    default_token），通过 OpenList 管理员 API 管理普通用户。
    """

    def __init__(self, plugin):
        super().__init__(plugin)
        # qq -> { "username", "password", "base_path" } 映射缓存（存本地配置文件）
        self._accounts_file = os.path.join(self.plugin.global_config_manager.config_dir, "openlist_accounts.json")
        self._accounts = self._load_accounts()

    def _load_accounts(self) -> Dict:
        try:
            if os.path.exists(self._accounts_file):
                with open(self._accounts_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            logger.error(f"加载 OpenList 账户映射失败: {e}", exc_info=True)
        return {}

    def _save_accounts(self):
        try:
            with open(self._accounts_file, "w", encoding="utf-8") as f:
                json.dump(self._accounts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 OpenList 账户映射失败: {e}", exc_info=True)

    def _admin_credentials(self) -> Optional[Dict]:
        """获取用于调用 OpenList 管理员 API 的凭据。"""
        try:
            cfg = self.plugin.get_global_config()
        except Exception as e:
            logger.error(f"读取全局配置失败: {e}", exc_info=True)
            return None
        url = (cfg.get("openlist_url") or "").strip()
        if not url:
            logger.warning("未配置 OpenList URL，无法进行账户管理。")
            return None
        username = (cfg.get("username") or "").strip()
        password = (cfg.get("password") or "").strip()
        token = (cfg.get("token") or "").strip()
        if not username and not token:
            logger.warning("未配置 OpenList 管理员用户名/口令，无法进行账户管理。")
            return None
        return {
            "url": url,
            "public_url": (cfg.get("public_openlist_url") or "").strip(),
            "username": username,
            "password": password,
            "token": token,
        }

    async def _admin_client(self):
        """创建连到管理员凭据的 OpenList 客户端（async with 方式使用）。"""
        # 延迟导入避免循环
        from ..lib.client import OpenlistClient
        cred = self._admin_credentials()
        if not cred:
            return None
        return OpenlistClient(
            cred["url"],
            cred.get("public_url", ""),
            cred.get("username", ""),
            cred.get("password", ""),
            cred.get("token", ""),
        )

    def _generate_password(self, length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _submit_base_path(self) -> str:
        """账户的基础路径 = default_submit_path（投稿模式根路径）。"""
        try:
            cfg = self.plugin.get_global_config()
        except Exception:
            return "/"
        root = (cfg.get("default_submit_path") or "").strip()
        return self.plugin._normalize_openlist_path(root) if root else "/"

    def _stored_account(self, qq: str) -> Optional[Dict]:
        return self._accounts.get(str(qq))

    def _get_username(self, qq: str) -> str:
        return str(qq).strip()

    # ------------------------------------------------------------------
    # 创建 / 查询 / 删除 / 重置密码
    # ------------------------------------------------------------------

    async def create_account(self, qq: str) -> Dict:
        """为白名单用户创建 OpenList 账户（用户名=QQ号，密码随机）。

        返回 {"ok": bool, "message": str, "username": str, "password": str}
        """
        qq = self._get_username(qq)
        username = qq
        base_path = self._submit_base_path()
        password = self._generate_password()

        # 已存在映射则直接返回（幂等，避免重复建号）
        exist = self._stored_account(qq)
        if exist:
            return {
                "ok": True,
                "message": f"ℹ️ 用户 {qq} 的 OpenList 账户已存在。",
                "username": exist.get("username", qq),
                "password": "(已设置)",
                "base_path": exist.get("base_path", base_path),
            }

        client = await self._admin_client()
        if client is None:
            return {"ok": False, "message": "❌ 无法连接 OpenList 管理端（管理员凭据未配置）。"}

        async with client:
            # 若同名用户已存在（如 WebUI 手动创建过），则复用而不覆盖密码
            remote = await client.find_user_by_username(username)
            if remote:
                self._accounts[qq] = {
                    "username": username,
                    "password": "",
                    "base_path": remote.get("base_path", base_path),
                }
                self._save_accounts()
                return {
                    "ok": True,
                    "message": f"✅ 发现已存在的 OpenList 账户（用户名 {username}），已纳入管理。",
                    "username": username,
                    "password": "(保留原密码)",
                    "base_path": remote.get("base_path", base_path),
                }

            result = await client.create_user(
                username=username,
                password=password,
                base_path=base_path,
                permission=SUBMIT_ACCOUNT_PERMISSION,
            )
            if not result:
                return {
                    "ok": False,
                    "message": f"❌ 创建 OpenList 账户失败（用户名 {username}）。请检查管理员凭据或查看日志。",
                }

            self._accounts[qq] = {
                "username": username,
                "password": password,
                "base_path": base_path,
            }
            self._save_accounts()
            return {
                "ok": True,
                "message": f"✅ 已为 QQ {qq} 创建 OpenList 账户。",
                "username": username,
                "password": password,
                "base_path": base_path,
            }

    async def delete_account(self, qq: str) -> Dict:
        """删除白名单用户的 OpenList 账户（用户名=QQ号）。"""
        qq = self._get_username(qq)
        username = qq
        client = await self._admin_client()
        if client is None:
            return {"ok": False, "message": "❌ 无法连接 OpenList 管理端（管理员凭据未配置）。"}

        delete_succeeded = False
        async with client:
            remote = await client.find_user_by_username(username)
            if remote:
                delete_succeeded = await client.delete_user(remote.get("id"))
                if not delete_succeeded:
                    return {
                        "ok": False,
                        "message": f"❌ 调用 OpenList 删除账户失败（用户名 {username}）。请查看日志。",
                    }
            # 无论远端是否存在，都清理本地映射
            self._accounts.pop(qq, None)
            self._save_accounts()
            if delete_succeeded:
                return {"ok": True, "message": f"✅ 已删除 OpenList 账户（用户名 {username}）。"}
            return {
                "ok": True,
                "message": f"ℹ️ 未找到 OpenList 账户（用户名 {username}），已清理本地记录。",
            }

    async def reset_password(self, qq: str) -> Dict:
        """重置用户自身 OpenList 账户密码并返回新密码。"""
        qq = self._get_username(qq)
        username = qq
        base_path = self._submit_base_path()
        new_password = self._generate_password()

        client = await self._admin_client()
        if client is None:
            return {"ok": False, "message": "❌ 无法连接 OpenList 管理端（管理员凭据未配置）。"}

        async with client:
            remote = await client.find_user_by_username(username)
            if not remote:
                return {
                    "ok": False,
                    "message": f"❌ 未找到 OpenList 账户（用户名 {username}）。请先通过 /素材 白名单 增加QQ 开通。",
                }
            ok = await client.update_user(
                user_id=remote.get("id"),
                username=username,
                base_path=remote.get("base_path") or base_path,
                password=new_password,
                permission=remote.get("permission", SUBMIT_ACCOUNT_PERMISSION) or SUBMIT_ACCOUNT_PERMISSION,
                disabled=remote.get("disabled", False) or False,
                role=remote.get("role", 0),
            )
            if not ok:
                return {"ok": False, "message": f"❌ 重置密码失败（用户名 {username}）。请查看日志。"}
            self._accounts[qq] = {
                "username": username,
                "password": new_password,
                "base_path": remote.get("base_path") or base_path,
            }
            self._save_accounts()
            return {
                "ok": True,
                "message": "🔑 已重置密码。",
                "username": username,
                "password": new_password,
                "base_path": remote.get("base_path") or base_path,
            }