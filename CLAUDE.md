# astrbot_plugin_twitanime 开发规范

## 项目简介
基于 Twikit + WD14 ONNX + Gemini 多模态 AI 的 AstrBot 推特二次元插画自动化抓取与审美精选插件。

## 成本意识
- 思考前先问：这个任务真的需要 Opus 吗？默认使用 Sonnet。
- 如果 Sonnet 两次尝试都没解决，再切换到 Opus。
- 大文件阅读前先问：是否真的需要完整内容？用小范围的关键字搜索代替。

## 输出与读取
- 只输出关键结论，不要解释过程。
- 用 @文件 引用代替手动敲路径。
- 读取工具输出时自动过滤噪音（如测试日志只保留失败信息）。

## 上下文管理
- 同任务的对话可以保留，但任务完成后主动提醒用户 /clear。
- 压缩点设在 50% 上下文利用率时。

## 项目结构

```
astrbot_plugin_twitanime/
├── main.py                  # 插件入口，注册精选/抓取/点赞/定时指令
├── metadata.yaml            # 插件元数据
├── _conf_schema.json        # WebUI 配置项定义
├── requirements.txt         # 依赖
├── CLAUDE.md                # 本文件
├── README.md
├── LICENSE
├── logo.png
├── models/wd14/
│   ├── model.onnx           # WD14 ONNX 模型文件
│   └── selected_tags.csv    # 标签映射表
└── utils/
    ├── __init__.py
    ├── twitter_fetcher.py   # Twitter API 封装 (Twikit)
    ├── cookie_manager.py    # Cookie 规范化与保存
    ├── wd14_filter.py       # WD14 ONNX 本地粗筛
    ├── ai_evaluator.py      # Gemini 多模态 AI 审美评估
    └── dedup_manager.py     # SQLite 去重管理
```

## 核心数据流
```
用户 /Ximage N
  → Twikit 分页抓取 Timeline (Producer)
  → SQLite 去重拦截
  → 下载标准图 → WD14 黑白名单粗筛 → Gemini 审美打分
  → 放行: 补拉原图 → 推送消息 → 写入去重库 (Consumer)
```

## 关键配置
- **twitter_section**: cookie_json (Twitter Cookies), proxy
- **gemini_section**: gemini_api_key（template_list 类型，每项一个 Key）, model_name, score_threshold
- **filter_section**: wd14_enabled（WD14 开关，默认关闭）, wd14_threshold, whitelist_tags, must_reject_tags, reject_business_user
- **storage_section**: auto_like, save_local, download_orig
- **schedule_section**: enabled, times (HH:MM 逗号分隔), count

## 定时投放
后台通过 `@on_astrbot_loaded()` + `asyncio.create_task` 启动永久循环，每 30 秒检查一次。
到点时调用 `_select_and_prepare_tweets()` 进行精选，通过 `context.send_message(session, chain)` 主动推送到绑定的群/会话。

指令（每组分指令别名等价）：
- `/xs bind` / `/定时 bind` — 绑定当前群为投放目标（存 `plugin_config` 表）
- `/xs unbind` / `/定时 unbind` — 解绑
- `/xs status` / `/定时 status` — 查看状态
- `/xs now` / `/定时 now` — 立即触发一次

## 去重行为
- `is_processed()` 默认检查任意状态的记录（兼容 `fetch_push` 的 success/filtered 拦截）
- 点赞去重 (`action_type="like"`) 必须传 `status="success"`，避免失败记录阻挡重试
- `schedule_run_log` 表按 `(slot_key, run_date)` 唯一约束防同一天重复投放

## 数据目录
