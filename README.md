# 🎨 AstrBot 推特二次元插画精选插件 (astrbot_plugin_twitanime)

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17.0-blue.svg)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

基于 **Twikit** 自动化数据抓取、**SQLite 持久化去重**、**WD14 ONNX** 本地 CPU 快速标签粗筛与 **Google Gemini 3.1 Flash** 多模态 AI 审美精选的 AstrBot 推特二次元插画推送插件。

---

## 🌟 核心特性

- 💾 **SQLite 本地持久化去重**：基于轻量级数据库维护推文历史处理记录（预留 `action_type` 与 `status` 扩展），已被过滤或推送过的推文自动零开销拦截，避免重复下载与 AI 评估耗费额度。
- ⚡ **异步延迟加载原图 (Lazy Load)**：评估阶段优先下载轻量标准图进行 WD14 粗筛与 Gemini 审美判定；评估通过后再补拉最高清原图（`orig`）发送，极大地提升 API 响应速度与吞吐量，省时省带宽。
- 🔑 **高可用 AI 引擎 (多 Key 轮询 + 退避重试)**：底层适配 Google 官方最新 `google-genai` SDK，采用 Client 实例模式与 JSON 原生结构化输出。支持配置多个 API Key 轮询使用，并在触发 429 Rate Limit 或 503 异常时自动退避重试（2s -> 4s -> 8s）。
- 🔄 **流式连续检索 (Cursor Pagination)**：自动分页向下获取 Twitter Timeline，不凑满用户请求的合格推文数量誓不罢休。
- 🏷️ **WD14 本地 CPU 粗筛**：基于 ONNX Runtime 进行本地轻量化推理，通过黑白名单标签（如过滤真实照片、男主、截图等）秒级剔除不符要求的图片，省 API 额度。
- 🤖 **Gemini 多维审美精选**：从画风完成度、色彩光影、构图及头部完整性等多维度综合打分，拒绝崩坏图与切割碎图。
- 🚀 **边查边发 (Stream Response)**：发现符合标准的插画推文立即单独推送，无需等待全部批次检索完成。

---

## 🏗️ 工作流程架构

```text
用户触发 /Ximage <推文数量>
  │
  ├── 1. 发送开场回复: "正在为您检索并精选 N 条合格插画推文..."
  │
  ├── 2. Twikit 分页抓取 Timeline (Producer)
  │      └── 读取 cookies.json 获取最新推文列表
  │
  ├── 3. SQLite 持久化去重拦截 (Dedup Check)
  │      └── 校验 tweet_history 表，已处理/已过滤推文直接跳过 (Zero Cost)
  │
  ├── 4. 评估阶段：低带宽下载标准图 + 双重粗筛精选
  │      ├── [Worker 1] 本地 WD14 ONNX 快速粗筛 (校验黑白名单)
  │      └── [Worker 2] Gemini 多模态 AI 审美精选 (多 Key 轮询 + 自动退避重试)
  │
  └── 5. 放行推送：原图补拉与状态持久化 (Consumer)
         ├── 根据配置补拉最高清原图 (orig)
         ├── 写入 SQLite 历史记录表 (status: success / filtered)
         └── 边查边发推送给用户，达到目标数量 N 后自动停调
```

---

## 📦 安装与配置

### 1. 依赖文件准备

确保插件目录下包含 WD14 ONNX 模型文件：

- `models/wd14/model.onnx`
- `models/wd14/selected_tags.csv`

> 💡 若未放置模型文件，插件将自动跳过本地粗筛环节，直接送往 Gemini 进行评估。

### 2. 配置项说明 (`_conf_schema.json`)

在 AstrBot 管理面板的插件配置中填写以下参数：

#### 🔐 推特配置 (`twitter_section`)

- **cookie_json**：使用浏览器插件导出的 Twitter `cookies.json`。
- **proxy**：访问 Twitter 与 Gemini API 所需的 HTTP/HTTPS 代理（如 `http://127.0.0.1:7890`）。

#### 🤖 Gemini AI 配置 (`gemini_section`)

- **gemini_api_key**：Gemini API Key（支持传入多个 Key，以英文逗号 `,` 或换行分隔，插件会自动轮询与失效切换）。
- **model_name**：默认使用 `gemini-3.1-flash-lite`（速度快、成本低、多模态理解能力强）。
- **score_threshold**：合格审美判定分（默认 `7.0`，满分 `10.0`）。

#### 🏷️ 标签过滤配置 (`filter_section`)

- **wd14_threshold**：WD14 标签判定置信度阈值（默认 `0.35`）。
- **whitelist_tags**：白名单标签，必须包含其中之一（例：`1girl, girls, virtual_youtuber`）。
- **must_reject_tags**：黑名单标签，命中即剔除（例：`screenshot, 3d, realistic, 1boy`）。

#### 💾 存储配置 (`storage_section`)

- **save_local**：保存精选插画到本地（`saved/` 目录）。
- **download_orig**：是否开启高清原图（`orig`）拉取模式。

---

## 🎮 使用指令

| 指令             | 说明                                           | 示例        |
| ---------------- | ---------------------------------------------- | ----------- |
| `/Ximage <数量>` | 精选并推送指定数量（1~10）的合格二次元插画推文 | `/Ximage 3` |

---

## 📄 开源协议

本项目采用 MIT License 开源协议。
