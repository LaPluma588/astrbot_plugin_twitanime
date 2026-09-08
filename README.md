# 🎨 AstrBot 推特二次元插画精选插件 (astrbot_plugin_twitanime)

[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.17.0-blue.svg)](https://github.com/Soulter/AstrBot)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

基于 **Twikit** 自动化数据抓取、**WD14 ONNX** 本地 CPU 快速标签粗筛与 **Gemini 3.1 Flash** 多模态 AI 审美精选的 AstrBot 推特二次元插画推送插件。

---

## 🌟 核心特性

- 🔄 **流式连续检索 (Cursor Pagination)**：自动分页向下获取 Twitter Timeline，不凑满用户请求的合格推文数量誓不罢休。
- ⚡ **WD14 本地 CPU 粗筛**：基于 ONNX Runtime 进行本地轻量化推理，通过黑白名单标签（如过滤真实照片、男主、截图等）秒级剔除不符要求的图片，极大地节省 Gemini API 调用额度。
- 🤖 **Gemini 审美打分**：调用 Gemini 多模态大模型，从画风完成度、色彩光影、构图及头部完整性等多维度综合打分，拒绝崩坏图与切割碎图。
- 🚀 **边查边发 (Stream Response)**：发现符合标准的插画推文立即单独推送，无需等待全部批次检索完成。
- 🏷️ **清晰元信息**：推送附带推文作者（含 `@handle`）与推文 ID，方便溯源。

---

## 🏗️ 工作流程架构

```text
用户触发 /Ximage <推文数量>
  │
  ├── 1. 发送开场回复: "正在为您检索并精选 N 条合格插画推文..."
  │
  ├── 2. Twikit 分页抓取 (Producer)
  │      └── 读取 data/plugins/astrbot_plugin_twitanime/cookies.json 进行登录与 Timeline 获取
  │
  ├── 3. 本地 WD14 ONNX 快速粗筛 (Worker 1)
  │      └── 运行 CPU 推理，校验 Whitelist / MustReject 标签，命中黑名单直接跳过
  │
  ├── 4. Gemini 多模态 AI 审美精选 (Worker 2)
  │      └── 评估完成度、色彩与头部完整性，输出 JSON 结构化评估结果
  │
  └── 5. 边查边发 (Consumer)
         └── 只要推文内包含 pass == true 的图片，立刻单独推送给用户！
         └── 达到指定的合格推文数量 N 后，自动停止调度并清理临时文件

```

---

## 📦 安装与配置

### 1. 依赖文件准备

确保插件目录下包含 WD14 ONNX 模型文件：

- `models/wd14/model.onnx`
- `models/wd14/selected_tags.csv`

> 💡 _若未放置模型文件，插件将自动跳过本地粗筛环节，直接送往 Gemini 进行评估。_

### 2. 配置项说明 (`_conf_schema.json`)

在 AstrBot 管理面板的插件配置中填写以下参数：

#### 🔐 推特配置 (twitter_section)

- **cookie_json**：使用浏览器插件（如 _EditThisCookie_ 或 _Get cookies.txt LOCALLY_）导出的 Twitter `cookies.json`。
- **proxy**：访问 Twitter 与 Gemini API 所需的 HTTP/HTTPS 代理（如 `http://127.0.0.1:7890`）。

#### 🤖 Gemini AI 配置 (gemini_section)

- **gemini_api_key**：Gemini API Key。
- **model_name**：默认使用 `gemini-3.1-flash-lite`（速度快、成本低、多模态理解能力强）。
- **score_threshold**：合格审美判定分（默认 `7.0`，满分 `10.0`）。

#### 🏷️ 标签过滤配置 (filter_section)

- **wd14_threshold**：WD14 标签判定置信度阈值（默认 `0.35`）。
- **whitelist_tags**：白名单标签，必须包含其中之一（例：`1girl, girls, virtual_youtuber`）。
- **must_reject_tags**：黑名单标签，命中即剔除（例：`screenshot, 3d, realistic, 1boy`）。

---

## 🎮 使用指令

| 指令             | 说明                                           | 示例        |
| ---------------- | ---------------------------------------------- | ----------- |
| `/Ximage <数量>` | 精选并推送指定数量（1~10）的合格二次元插画推文 | `/Ximage 3` |

---

## 📄 开源协议

本项目采用 [MIT License](https://www.google.com/search?q=LICENSE) 开源协议。

# Supports

- [AstrBot Repo](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot Plugin Development Docs (Chinese)](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot Plugin Development Docs (English)](https://docs.astrbot.app/en/dev/star/plugin-new.html)
