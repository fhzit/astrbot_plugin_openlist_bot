<div align="center">

# <img src="https://raw.githubusercontent.com/OpenListTeam/Logo/main/logo.svg" width="32" height="32" style="vertical-align: middle;"> OpenList 助手

<i>🚀 跨越终端，触手可及的素材管理专家</i>

![License](https://img.shields.io/badge/license-AGPL--3.0-green?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white)
![AstrBot](https://img.shields.io/badge/framework-AstrBot-ff6b6b?style=flat-square)

</div>

## ✨ 简介

一款为 [**AstrBot**](https://github.com/AstrBotDevs/AstrBot) 设计的 [**OpenList**](https://github.com/OpenListTeam/OpenList) 文件管理插件。它将强大的素材管理功能带入聊天界面，让您可以像聊天一样轻松列出、搜索、下载、上传文件，支持智能导航、文件预览等多种高级特性。

本项目参考 [**Foolllll-J/astrbot_plugin_openlistfile**](https://github.com/Foolllll-J/astrbot_plugin_openlistfile) 进行重构二次开发，在保留原有业务能力的基础上，重新梳理了项目结构、配置逻辑和下载/上传流程。

---

## ✨ 功能特性

* 📁 **智能导航** - 序号快速导航，支持进入文件夹、返回上级目录和分页浏览，并按会话隔离状态。
* 📥 **直接下载** - 通过 OpenList API 获取真实下载地址，并使用 AstrBot `File` 组件发送文件。
* 🔗 **链接获取** - 下载链接会以 txt 附件发送，避免长链接被平台自动转成图片。
* 📤 **文件上传** - 先发送图片、视频或文件，再使用 `素材 上传` 上传同会话最近 5 分钟内的附件消息。
* 🔍 **文件搜索** - 支持在指定目录中搜索目标文件。
* 📋 **文件信息** - 查看文件详细信息，并可附带下载链接 txt 附件。
* 👁️ **内容预览** - 支持文本文件预览和压缩包内容查看。
* ⚙️ **灵活设置** - 支持全局设置和用户独立设置两种模式。
* 🧰 **传输调优** - 上传分块、超时、重试和诊断日志均可配置。
* 🎨 **美化显示** - 智能文件图标，直观的信息展示。

---

## 🔧 设置方式

### 🔌 两种设置模式

#### 1. 全局设置模式（默认推荐）

* 所有用户共享同一个 OpenList 服务器连接。
* 管理员在 AstrBot WebUI 中统一设置。
* 适合团队共享同一个文件服务器的场景。

#### 2. 用户独立设置模式

* 每个用户拥有独立的 OpenList 连接设置。
* 用户设置互不干扰，支持连接不同的 OpenList 服务器。
* 启用 `require_user_auth` 后，每位用户需要自行配置连接信息。

### 🖥️ WebUI 全局设置

首次加载后，请在 AstrBot 后台 -> 插件 页面找到本插件进行设置。

常用全局配置项：

| 配置项 | 说明 |
| :--- | :--- |
| `default_openlist_url` | OpenList API 地址（机器人访问）。普通部署直接填公网地址；Docker/内网部署可填内网地址 |
| `public_openlist_url` | 对外下载地址（可选）。仅 API 地址为内网、但发给用户的链接需要公网访问时填写 |
| `default_username` | 默认用户名，留空表示匿名访问 |
| `default_password` | 默认密码 |
| `default_token` | 默认访问 Token，优先级高于用户名密码 |
| `fixed_base_directory` | 路径前缀修正（高级，可选）。仅列表路径与真实下载路径不一致时填写 |
| `require_user_auth` | 是否要求每个用户独立配置 |
| `allowed_extensions` | 允许的文件扩展名，留空表示不限制 |
| `default_submit_path` | 投稿模式默认根路径。填写后开启投稿隔离（详见下文「投稿模式」）；留空关闭 |

> 如果只配置了用户名和密码，插件会自动登录 OpenList 并获取 Token；不需要手动填写 Token。
>
> 大多数公网单地址部署只需要填写 `default_openlist_url`，`public_openlist_url` 和 `fixed_base_directory` 都可以留空。

### 💬 用户设置（聊天界面）

#### 快速设置向导

```
素材 配置 向导
```

#### 手动设置

**Bash**

```
# 显示当前设置
素材 配置 查看

# 设置 OpenList API 地址（机器人访问）
素材 配置 设置 openlist_url http://your-server:5244

# 设置用户名（可选）
素材 配置 设置 username your_username

# 设置密码（可选）
素材 配置 设置 password your_password

# 设置访问 Token（可选，优先级高于用户名密码）
素材 配置 设置 token your_token

# 设置对外下载地址（可选；普通部署留空）
素材 配置 设置 public_openlist_url https://your-public-domain

# 设置路径前缀修正（高级可选；普通部署留空）
素材 配置 设置 fixed_base_directory /夸克

# 设置允许的文件扩展名（留空表示不限制）
素材 配置 设置 allowed_extensions .txt,.pdf,.mp4

# 设置最大下载 / 上传 / 预览大小（MB，0 表示不限制）
素材 配置 设置 max_download_size 50
素材 配置 设置 max_upload_size 100
素材 配置 设置 max_preview_size 10

# 测试连接
素材 配置 测试

# 清理文件列表缓存
素材 配置 清缓存
```

#### 传输调优

**Bash**

```
# 普通上传单文件重试：总尝试次数 3，每次间隔 5 秒
素材 配置 设置 upload_retry_attempts 3
素材 配置 设置 upload_retry_delay 5

# 传输调优
素材 配置 设置 upload_chunk_size_mb 4
素材 配置 设置 upload_progress_step_mb 64
素材 配置 设置 upstream_connect_timeout 60
素材 配置 设置 upstream_read_timeout 180
素材 配置 设置 openlist_connect_timeout 30
素材 配置 设置 openlist_upload_response_timeout 3000

# 开启上传/下载/DNS 诊断日志（默认关闭）
素材 配置 设置 debug_transfer_logging true
```

---

## 📖 使用指南

### 📝 指令列表

主指令为全中文「素材」。以下是全部指令：

> 发送 `素材` 或 `素材 帮助` 可查看完整帮助。子命令缺少参数或参数格式错误时，插件会返回对应的简短用法、示例和必要提示。

| 指令 | 说明 |
| :--- | :--- |
| `素材 列表 [路径/序号]` | 列出文件/进入子目录/获取下载链接（txt 附件） |
| `素材 配置 <查看/设置/向导/测试/清缓存>` | 配置插件参数 |
| `素材 下一页` | 列表翻页（下一页） |
| `素材 上一页` | 列表翻页（上一页） |
| `素材 搜索 <关键词> [路径]` | 搜索文件 |
| `素材 信息 <路径>` | 查看文件/目录详细信息 |
| `素材 下载 <路径/序号>` | 直接下载文件并发送 |
| `素材 上传 [说明]` | 上传最近附件消息中的图片、视频或文件 |
| `素材 预览 <路径/序号>` | 预览文本或压缩包 |
| `素材 删除 <路径/序号>` | 删除文件或目录 |
| `素材 新建 <名称/路径>` | 创建新目录 |
| `素材 上一级 / 素材 返回` | 返回上级目录 |
| `素材 帮助` | 显示帮助信息 |

### 📂 浏览与导航

**Bash**

```
# 查看帮助文档
素材 帮助

# 列出根目录文件
素材 列表 /

# 使用序号进入子目录
素材 列表 1          # 如果 1 号是目录，则进入该目录

# 如果序号对应文件，则获取下载链接 txt 附件
素材 列表 2

# 翻页
素材 下一页          # 查看下一页
素材 上一页          # 查看上一页

# 返回上级目录
素材 上一级

# 路径方式
素材 列表 /movies    # 列出 /movies 目录的内容
```

### 🔍 文件搜索与信息

**Bash**

```
# 搜索文件（注意：依赖服务器索引，结果可能非最新）
素材 搜索 年度报告

# 在指定目录搜索
素材 搜索 年度报告 /documents

# 查看文件信息
素材 信息 /movies/Inception.mkv

# 预览文件内容（支持文本和压缩包）
素材 预览 2
素材 预览 /data/config.txt

# 新建文件夹
素材 新建 my_folder
素材 新建 /data/new_dir

# 删除文件或文件夹（谨慎操作）
素材 删除 3
素材 删除 /temp/stale_file.txt
```

### 📥 下载与上传

**Bash**

```
# 方式一：获取下载链接（txt 附件）
素材 列表 2
素材 列表 /movies/Inception.mkv

# 方式二：直接下载文件
素材 下载 2
素材 下载 /movies/Inception.mkv

# 先发送图片、视频或文件，再上传到当前目录
素材 上传

# 先发送图片、视频或文件，再上传到指定目录
素材 上传 /movies

# 先发送图片、视频或文件，再上传到当前目录下的子目录
素材 上传 clips

# 先发送图片、视频或文件，上传到当前目录，并记录自定义说明
素材 上传 周日整理的资料

# 先发送图片、视频或文件，上传到指定目录，并记录自定义说明
素材 上传 /movies 周年庆素材
```

说明：

* `素材 列表` 获取文件链接时，会将下载链接写入 txt 附件发送，避免长文本被平台转成图片。
* `素材 下载` 会先通过 OpenList API 获取真实下载链接，再下载到本地临时文件并用 `File` 组件发送。

### 🔒 投稿模式（可选）

在 WebUI 全局设置中填写 `default_submit_path`（如 `/submit`）后开启投稿模式。开启后：

* 每个用户只能在其个人投稿文件夹 `<default_submit_path>/<QQ号>` 内使用 `素材 列表`、`素材 下载`、`素材 上传`，无法退出到上级目录或访问其他用户的文件夹（含显式路径访问，如 `素材 列表 /` 也会被拉回自己的文件夹）。
* `素材 上传` 文件自动重命名为 `{年月日小时分钟}_{自定义说明}.{原扩展名}`，例如 `素材 上传 我的生日` 上传 `photo.jpg` → `202608291400_我的生日.jpg`。同秒多文件自动追加序号（`_2`、`_3`...）。
* 投稿模式下 `素材 搜索`、`素材 删除`、`素材 新建` 不可用（返回提示）。
* 自动创建个人文件夹：用户首次操作时若 `<QQ号>` 目录不存在会自动创建。
* 留空 `default_submit_path` 即关闭投稿模式，恢复原有全局行为。
* `素材 上传` 使用同会话、同一发送者 5 分钟内最近一条附件消息；不依赖 QQ/OneBot 的引用回复解析。
* 最近附件缓存只保存消息元数据和 URL，不保存文件内容；缓存最多保留 500 个会话条目，并忽略机器人自己发出的消息。
* 上传使用平台提供的文件 URL 进行流式中转；群文件没有 URL 时会尝试通过 OneBot 群文件接口获取下载地址。
* 用户上传的 URL 流式中转多次失败后，会改用本地临时文件备用上传：先完整下载到 `upload_temp`，校验大小后再上传；无论成功或失败，都会清理临时文件。
* 浏览列表、分页和序号操作按会话隔离；同一用户在不同群聊或私聊中使用不会串用序号状态。

---

## 📜 项目说明

### ⚙️ 配置说明

首次加载后，请在 AstrBot 后台 -> 插件 页面找到本插件进行设置。所有配置项都有详细的说明和提示。

### 📂 文件存储结构

```
data/plugins_data/openlist/
├── global_config.json          # 全局设置文件
├── users/                      # 用户设置目录
│   ├── user1.json              # 用户 1 的设置
│   ├── user2.json              # 用户 2 的设置
│   └── ...
├── cache/                      # 文件列表缓存目录
│   ├── abc123.json             # 缓存文件 (MD5 命名)
│   └── ...
├── downloads/                  # 临时下载目录
│   ├── user123_xxx_file.txt    # 临时下载文件
│   └── ...
├── links/                      # 下载链接 txt 临时目录
├── temp_preview/               # 文件预览临时目录
└── upload_temp/                # 用户上传备用上传临时目录
```

### 🧱 重构后的源码结构

```
astrbot_plugin_openlist_bot/
├── main.py                     # 插件入口、命令注册和通用工具
├── lib/
│   ├── cache.py                # 文件列表缓存
│   ├── client.py               # OpenList API 客户端
│   └── config.py               # 配置默认值和校验规则
└── services/
    ├── base.py                 # 服务基类与共享临时文件工具
    ├── browse.py               # 浏览、搜索、删除、新建
    ├── config_command.py       # 配置命令
    ├── download.py             # 下载与链接发送
    ├── preview.py              # 文件预览
    ├── upload.py               # 最近附件上传
    └── help.py                 # 帮助信息
```

AstrBot 插件要求入口类和命令注册保留在 `main.py` 中，因此重构后仍由 `main.py` 注册命令，具体业务逻辑拆分到 `services/`。

---

## 🛠️ 故障排除

### ❓ 常见问题

**Q: 提示“❌ 请先配置 OpenList 连接信息”**

A: 这是因为您处于“用户独立设置模式”，或全局 OpenList 地址尚未设置。请运行 `素材 配置 向导` 设置向导，或在 WebUI 中配置默认 OpenList 服务器地址。

**Q: 只配置了账号密码，没有配置 Token，可以用吗？**

A: 可以。插件会在创建 OpenList 客户端时自动登录并获取 Token。Token 是可选项，并且优先级高于用户名密码。

**Q: 为什么下载链接要作为 txt 附件发送？**

A: AstrBot 配置会把长文本消息转换成图片，导致直链不可复制。插件会将链接写入 txt 附件发送，避免这个问题。

**Q: 只发送 `素材` 时会显示什么？**

A: 插件会直接显示整理过的帮助信息，避免 AstrBot 指令组默认树形提示过长、参数类型噪声过多的问题。

**Q: `fixed_base_directory` 这个参数有用吗？**

A: 有用，但它是高级兼容项，普通部署请留空。它只用于修正 `/d` 下载链接和 `/api/fs/link` 真实下载链接的路径前缀。典型场景是：OpenList 列表里看到的文件路径是 `/video/a.mp4`，但真实下载接口要求的路径是 `/夸克/video/a.mp4`，这时才需要填写 `/夸克`。它不是默认浏览目录，也不是备份目录；填错会导致下载链接或直接下载失败。

**Q: 为什么 `搜索` 搜不到文件，但 `列表` 能看到？**

A: 这是因为 `搜索` 依赖服务器的**搜索索引**，而 `列表` 是实时列出文件。如果文件是新添加的，服务器索引可能尚未更新。请联系您的 OpenList 服务器管理员，在后台对相应存储**手动更新索引**。

**Q: `/api/fs/link` 返回 403 或提示不是管理员怎么办？**

A: 部分 OpenList 权限配置要求管理员才能调用真实下载链接接口。请确认插件配置的账号具有对应权限；如果只需要链接，可使用 `素材 列表 文件路径` 获取普通下载链接 txt 附件。

**Q: 连接测试失败**

A: 请检查：

1. 服务器地址是否正确（包含 `http://` 或 `https://`）；
2. AstrBot 所在设备网络是否能访问到该地址；
3. 用户名、密码或 Token 是否正确；
4. 如果使用公网下载链接，请确认 `public_openlist_url` 是否可访问。

### ✅ 设置验证

使用以下指令验证设置：

**Bash**

```
素材 配置 查看    # 查看当前设置
素材 配置 测试    # 测试连接
素材 列表 /       # 测试文件列表
```

---

## 🔄 版本历史

详见 [CHANGELOG.md](./CHANGELOG.md)。

---

## 🙏 致谢

本项目参考 [astrbot_plugin_openlistfile](https://github.com/Foolllll-J/astrbot_plugin_openlistfile) 进行重构二次开发，在此向原作者表示衷心感谢！


---

## ❤️ 支持

* [AstrBot 帮助文档](https://docs.astrbot.app/)
* 如果您在使用中遇到问题，欢迎在仓库提交 Issue。

---

<div align="center">

**如果本插件对你有帮助，欢迎点个 ⭐ Star 支持一下！**

</div>
