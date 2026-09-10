import os
import re
import json
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional

from google import genai
from google.genai import types
from PIL import Image

from astrbot.api import logger

SYSTEM_PROMPT = """
你是一位顶级的二次元插画画师和审美术师。请对输入的图片进行严格的分类检查与审美评估，并严格按 JSON 格式返回结果。
【第一阶段：硬性一票否决规则（只要命中任意一条，pass 必须为 false，score 设为 0）】
1. 动画/游戏截图：包含动画字幕、游戏 UI/血条/按键、视频播放框，或明显的帧截图画风。
2. 真实/实体照片：包含现实生活照片、手办/玩偶/Cosplay 摄影、实体周边的拍照。
3. 画面不完整/拼图割裂（注意：允许并接受画风完整的多格短条漫、四格漫画）：
- 单张插画中人物头部被裁剪截断或不完整（例如仅露出下巴/身体/缺失头部）。
- 属于将“同一张插画”故意切碎、放大局部展示的橱窗拼接碎片图。
* 特别说明：如果是创作完整的多格漫画（Comic / Manga panel）、短四格插图，只要画面完整且画风优秀，【不属于】拼图割裂，应当正常评估。
【第二阶段：二次元插画审美打分（仅在通过第一阶段后评估）】
1. 画风与完成度：线稿干净、细节丰富、无明显 AI 手指/五官崩坏。
2. 色彩与光影：色彩协调、光影层次丰富。
3. 构图与视觉冲击力：构图合理、人物主体突出。

请输出 JSON 格式（不要使用 ```json 包裹），包含以下两个字段：
- "score": 浮点数，范围 1.0 到 10.0，表示综合审美得分。
- "reason": 字符串，简短说明打分原因（20字以内）。
"""

class AIEvaluator:
    def __init__(self, api_keys: List[str], model_name: str = "gemini-3.1-flash-lite", score_threshold: float = 7.0, proxy: Optional[str] = None):
        self.api_keys = [k.strip() for k in api_keys if k.strip()]
        self.model_name = model_name
        self.score_threshold = score_threshold
        self.proxy = proxy
        self.current_key_idx = 0

        if self.proxy:
            os.environ["http_proxy"] = self.proxy
            os.environ["https_proxy"] = self.proxy

        if not self.api_keys:
            logger.error("[Twitanime] ❌ 未配置任何有效的 Gemini API Key！")

    def _get_current_key(self) -> str:
        """获取当前使用的 API Key"""
        if not self.api_keys:
            return ""
        return self.api_keys[self.current_key_idx % len(self.api_keys)]

    def _rotate_key(self) -> str:
        """轮换至下一个 API Key"""
        if not self.api_keys:
            return ""
        self.current_key_idx = (self.current_key_idx + 1) % len(self.api_keys)
        next_key = self._get_current_key()
        logger.info(f"[Twitanime] 🔄 已自动轮换至 API Key [索引: {self.current_key_idx}] (...{next_key[-6:] if len(next_key) > 6 else ''})")
        return next_key

    async def evaluate(self, image_path: str, max_retries: int = 3) -> Dict[str, Any]:
        """
        基于 google-genai 最新 SDK 进行 AI 审美打分，内置多 Key 轮询与退避重试
        """
        if not self.api_keys:
            return {"pass": False, "score": 0.0, "reason": "未配置 Gemini API Key"}

        retry_count = 0
        backoff_delay = 2.0

        while retry_count <= max_retries:
            current_key = self._get_current_key()
            try:
                # 使用 google-genai 的全新 Client 实例化方式
                client = genai.Client(api_key=current_key)

                # 打开并读取图像
                image = Image.open(image_path)

                # 调用 Client 生成内容 (通过 asyncio.to_thread 防止阻塞主事件循环)
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model_name,
                    contents=[SYSTEM_PROMPT, image],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )

                text_resp = response.text.strip() if response.text else ""
                json_match = re.search(r'\{.*\}', text_resp, re.DOTALL)
                if not json_match:
                    logger.warning(f"[Twitanime] ⚠️ Gemini 返回格式不可解析: {text_resp}")
                    return {"pass": False, "score": 0.0, "reason": "响应格式异常"}

                data = json.loads(json_match.group())
                score = float(data.get("score", 0.0))
                reason = data.get("reason", "无评语")
                is_pass = score >= self.score_threshold

                return {
                    "pass": is_pass,
                    "score": score,
                    "reason": reason
                }

            except Exception as e:
                err_msg = str(e)
                is_transient_error = any(code in err_msg for code in ["503", "429", "UNAVAILABLE", "ResourceExhausted"])

                if is_transient_error and retry_count < max_retries:
                    retry_count += 1
                    logger.warning(
                        f"[Twitanime] ⚠️ Gemini API 繁忙/限流。将在 {backoff_delay}s 后重试 [{retry_count}/{max_retries}] 并切换 Key..."
                    )
                    self._rotate_key()
                    await asyncio.sleep(backoff_delay)
                    backoff_delay *= 2
                else:
                    logger.error(f"[Twitanime] ❌ Gemini 打分 API 调用失败: {err_msg}")
                    self._rotate_key()
                    return {"pass": False, "score": 0.0, "reason": f"API 异常: {err_msg[:30]}"}

        return {"pass": False, "score": 0.0, "reason": "多次重试后仍处于高负荷状态"}