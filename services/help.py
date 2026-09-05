from astrbot.api.event import AstrMessageEvent

from .base import PluginService


class HelpService(PluginService):
    """Help service."""

    async def help_command(self, event: AstrMessageEvent):
        """显示帮助信息"""
        user_id = event.get_sender_id()
        is_whitelisted = self.is_whitelisted_user(user_id)
        is_advanced = self.is_advanced_whitelisted_user(user_id)

        # 角色划分：
        #   普通用户 = 非白名单；干事 = 普通白名单；干部 = 高级白名单
        if is_advanced:
            role = "干部"
        elif is_whitelisted:
            role = "干事"
        else:
            role = "普通用户"

        header = f"""📚 素材助手 · 帮助
👤 当前身份：{role}

"""

        # === 普通用户 / 干事 / 干部 都会显示：查看自己上传的视频 ===
        body_common = """📁 查看自己已上传的视频目录
   `素材 列表` — 进入并浏览你自己上传的视频文件夹
   `素材 上一级` — 返回上级目录
   `素材 上一页` / `素材 下一页` — 翻页
   `素材 新建 <文件夹名>` — 新建文件夹
   `素材 删除 <序号/路径>` — 删除文件或文件夹（非白名单用户仅可操作自己投稿文件夹内的内容）

📤 上传素材
   请直接【私聊】机器人发送图片、视频或文件，系统会自动上传到你的个人文件夹（按日期归档）。
"""

        # === 干事 / 干部 额外：云盘账户 ===
        body_staff = """🌐 云盘网站
   `素材 网站` — 获取云盘访问网址，并显示你的登录用户名
   `素材 重置密码` — 重置你的 OpenList 账户密码（重置后牢记新密码）
"""

        # === 干部 额外：编辑干事权限（白名单管理） ===
        body_cadre = """🛡️ 编辑干事权限
   `素材 白名单 增加 <QQ号>` — 将用户设为干事
   `素材 白名单 删除 <QQ号>` — 取消某用户的干事身份
   `素材 白名单 查看` — 查看当前干事名单
"""

        if role == "普通用户":
            body = body_common
        elif role == "干事":
            body = body_common + "\n" + body_staff
        else:  # 干部
            body = body_common + "\n" + body_staff + "\n" + body_cadre

        yield event.plain_result(header + body)