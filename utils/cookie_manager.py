import os
import json
from pathlib import Path
from typing import Union

class CookieManager:
    @staticmethod
    def sanitize_and_save(cookie_input: Union[str, list, dict], target_path: Path) -> bool:
        """
        解析并规范化 Cookie，保存到 target_path
        """
        try:
            # 1. 解析输入的 JSON 字符串/对象
            if isinstance(cookie_input, str):
                cookie_input = cookie_input.strip()
                if not cookie_input:
                    data = []
                else:
                    data = json.loads(cookie_input)
            else:
                data = cookie_input

            # 2. 规范化为 twikit 所需的 Dict[str, str] 格式
            formatted_cookies = {}
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and 'name' in item and 'value' in item:
                        formatted_cookies[item['name']] = str(item['value'])
            elif isinstance(data, dict):
                formatted_cookies = {k: str(v) for k, v in data.items()}
            else:
                return False

            # 3. 确保目标文件夹存在并写入
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, 'w', encoding='utf-8') as f:
                json.dump(formatted_cookies, f, indent=4)
            return True
        except Exception as e:
            print(f"[Twitanime] Cookie 解析或保存失败: {e}")
            return False