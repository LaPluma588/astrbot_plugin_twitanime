# 🎨 AstrBot 推特二次元插画精选插件 (astrbot_plugin_twitanime)

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17.0-blue.svg)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

基于 **Twikit** 自动化数据抓取、**SQLite 持久化去重**、**WD14 ONNX** 本地 CPU 快速标签粗筛与 **Google Gemini 3.1 Flash** 多模态 AI 审美精选的 AstrBot 推特二次元插画推送插件。

---

## 🌟 核心特性

- ⏱️ **定时自动投放**：后台定时循环，到点自动精选推送插画到指定群聊，无需人工干预。
- ❤️ **自动/手动点赞支持**：精选通过的推文支持后台配置开启自动点赞；同时提供 `/xl`、`/赞` 指令以及 `@机器人 + 引用回复` 自然语言触发点赞双模式。
- 💾 **SQLite 本地持久化去重**：基于轻量级数据库维护推文历史处理记录，已被过滤或推送过的推文自动零开销拦截，避免重复下载与 AI 评估耗费额度。
- ⚡ **异步延迟加载原图 (Lazy Load)**：评估阶段优先下载轻量标准图进行 WD14 粗筛与 Gemini 审美判定；评估通过后再补拉最高清原图（`orig`）发送，极大地提升 API 响应速度与吞吐量。
- 🔑 **高可用 AI 引擎 (多 Key 轮询 + 退避重试)**：底层适配 Google 官方 `google-genai` SDK，支持配置多个 API Key（template_list 格式）轮询使用，在触发限流时自动退避重试与 Key 切换。
- 🎨 **自定义 AI 提示词**：支持在插件配置中写入自定义审核提示词，插件自动拼接 JSON 格式要求一并发送，灵活调整审美标准。
- 🔄 **流式连续检索 (Cursor Pagination)**：自动分页向下获取 Twitter Timeline，不凑满用户请求的合格推文数量誓不罢休。
- 🏷️ **WD14 本地 CPU 粗筛（可选开关）**：基于 ONNX Runtime 进行本地轻量化推理，通过黑白名单标签秒级剔除不符要求的图片，支持一键关闭。
- 🤖 **Gemini 多维审美精选**：从画风完成度、色彩光影、构图及头部完整性等多维度综合打分，拒绝崩坏图与切割碎图。
- 🚫 **金标/企业账号早期硬拦截**：支持在 Timeline 提取阶段直接过滤 Twitter 金标 (Business) 官方/企业账号发帖。

---

## 🏗️ 工作流程架构

```text
用户触发 /x <数量> 或定时到点
  │
  ├── 1. Twikit 分页抓取 Timeline (Producer)
  │      └── 读取 cookies.json 获取最新推文列表
  │
  ├── 2. SQLite 持久化去重拦截 (Dedup Check)
  │      └── 校验 tweet_history 表，已处理推文直接跳过
  │
  ├── 3. 评估阶段：低带宽下载标准图 + 双重粗筛精选
  │      ├── [Worker 1] 本地 WD14 ONNX 快速粗筛（可选，校验黑白名单）
  │      └── [Worker 2] Gemini 多模态 AI 审美精选（多 Key 轮询 + 自动退避重试）
  │
  └── 4. 放行推送：原图补拉与状态持久化 (Consumer)
         ├── 根据配置补拉最高清原图 (orig)
         ├── 写入 SQLite 历史记录表
         └── 推送给用户 / 定时投放自动推送到绑定群
```

---

## 📦 安装与配置

### 1. 环境与依赖安装

> 💡 如果使用 AstrBot 面板管理插件，依赖会自动安装，可跳过此步骤。

> ⚠️ **注意**：由于原版 `twikit` 存在部分底层 API 解析 Bug，本插件依赖了经过修补和维护的 `twikit` 分支。

如需手动安装，请在插件根目录下运行以下命令（需确保本地已安装 Git）：

```bash
pip install -r requirements.txt
```

如果是在面板环境或无法自动拉取 Git 仓库的环境中，可手动安装修补版 twikit：

```bash
pip install git+https://github.com/LaPluma588/twikit.git@main
```

### 2. 模型文件准备（仅开启 WD14 粗筛时需要）

请将以下文件放置到 `models/wd14/` 目录（可通过面板上传，或手动下载）：

```bash
# 1. 下载 WD14 v2 ONNX 模型文件
# 以 WD14 v2 ConvNeXt 为例（约 400MB）：
wget https://huggingface.co/SmilingWolf/wd-v1-4-convnextv2-tagger-v2/resolve/main/model.onnx -O models/wd14/model.onnx

# 2. 下载配套的标签映射文件
wget https://huggingface.co/SmilingWolf/wd-v1-4-convnextv2-tagger-v2/resolve/main/selected_tags.csv -O models/wd14/selected_tags.csv
```

### 3. 配置项说明 (`_conf_schema.json`)

在 AstrBot 管理面板的插件配置中填写以下参数：

#### 🔐 推特配置 (`twitter_section`)

- **cookie_json**：使用浏览器插件导出的 Twitter `cookies.json`。
- **proxy**：访问 Twitter 与 Gemini API 所需的 HTTP/HTTPS 代理（如 `http://127.0.0.1:7890`）。

#### 🤖 Gemini AI 配置 (`gemini_section`)

- **gemini_api_key**：template_list 类型，每条独立添加一个 Gemini API Key，支持多个 Key 轮询。
- **model_name**：默认使用 `gemini-3.1-flash-lite`（速度快、成本低、多模态理解能力强）。
- **score_threshold**：合格审美判定分（默认 `7.0`，满分 `10.0`）。
- **custom_prompt**：自定义 AI 审核提示词（可选），留空使用插件内置默认提示词。

#### 🏷️ 标签过滤配置 (`filter_section`)

- **wd14_enabled**：是否启用 WD14 本地粗筛（默认关闭，关闭后所有图片直接进入 Gemini 审核）。
- **wd14_threshold**：WD14 标签判定置信度阈值（默认 `0.35`）。
- **whitelist_tags**：白名单标签，必须包含其中之一（例：`1girl, girls, virtual_youtuber`）。
- **must_reject_tags**：黑名单标签，命中即剔除（例：`screenshot, 3d, realistic, 1boy`）。
- **reject_business_user**：是否自动拦截金标 (Business) 官方/企业账号推文（默认 true）。

#### 💾 存储配置 (`storage_section`)

- **auto_like**：精选通过后自动点赞推文。
- **save_local**：保存精选插画到本地（`saved/` 目录）。
- **download_orig**：是否开启高清原图（`orig`）拉取模式。

#### ⏱️ 定时投放配置 (`schedule_section`)

- **enabled**：开启定时投放。
- **times**：投放时刻，逗号分隔的 HH:MM（例：`08:00,20:00`）。
- **count**：每次投放图片数量。

---

## 🎮 使用指令

| 指令（别名）                    | 说明                                              |
| ------------------------------ | ------------------------------------------------- |
| `/Ximage` `/x` `/精选`         | 精选并推送指定数量（1~10）的合格二次元插画推文    |
| `/Xfetch` `/xf` `/抓取`        | 拉取指定推文 ID 的完整图文内容                    |
| `/Xlike` `/xl` `/赞`           | 点赞指定推文                                      |
| `/xs bind` `/定时 bind`        | 绑定当前群为定时投放目标                          |
| `/xs unbind` `/定时 unbind`    | 解绑定时投放目标                                  |
| `/xs status` `/定时 status`    | 查看定时投放配置状态                              |
| `/xs now` `/定时 now`          | 立即触发一次手动投放                              |
| `@Bot 喜欢/点赞`               | 回复图片并 @Bot 触发点赞                          |

---

## 📄 开源协议

本项目采用 MIT License 开源协议。
