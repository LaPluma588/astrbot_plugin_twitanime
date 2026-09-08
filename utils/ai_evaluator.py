import os
import json
import asyncio
from typing import Dict, Any, Optional

from google import genai
from google.genai import types
from astrbot.api import logger

class AIEvaluator:
    def __init__(self, api_key: str, model_name: str = "gemini-3.1-flash-lite", score_threshold: float = 7.0, proxy: Optional[str] = None):
        self.api_key = api_key
        self.model_name = model_name
        self.score_threshold = score_threshold
        
        if proxy:
            os.environ["HTTP_PROXY"] = proxy
            os.environ["HTTPS_PROXY"] = proxy
            
        self.client = genai.Client(api_key=self.api_key)

    async def evaluate(self, image_path: str) -> Dict[str, Any]:
        prompt = """
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

                请严格仅输出纯 JSON，格式规范如下：
                {
                "is_anime_screenshot": false,
                "is_real_photo": false,
                "has_complete_head": true,
                "score": 8.5,
                "pass": true,
                "reason": "画风精致，色彩与光影质感极佳"
                }
                """
        try:
            with open(image_path, "rb") as f:
                image_bytes = f.read()

            ext = os.path.splitext(image_path)[1].lower()
            mime_type = "image/png" if ext == ".png" else "image/jpeg"

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2
            )

            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    prompt
                ],
                config=config
            )

            res_text = (response.text or "").strip()
            if not res_text:
                raise ValueError("Gemini 返回的文本内容为空")

            data = json.loads(res_text)
            
            score = float(data.get("score", 0.0))
            is_pass = bool(data.get("pass", False))
            reason = str(data.get("reason", "无评语"))

            # 兜底校验：若命中了否决项，强制拦截
            if data.get("is_anime_screenshot") or data.get("is_real_photo") or not data.get("has_complete_head", True):
                is_pass = False
                score = 0.0

            return {
                "pass": is_pass and (score >= self.score_threshold),
                "score": score,
                "reason": reason
            }

        except Exception as e:
            logger.error(f"[Twitanime] ❌ Gemini 打分 API 调用失败: {repr(e)}")
            return {
                "pass": False,
                "score": 0.0,
                "reason": f"AI 评估异常: {str(e)}"
            }