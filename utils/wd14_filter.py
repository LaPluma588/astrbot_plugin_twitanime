import csv
import numpy as np
import cv2
import onnxruntime as ort
from pathlib import Path
from typing import Set, Tuple, List, cast

from astrbot.api import logger

class WD14Filter:
    def __init__(self, model_path: Path, tags_path: Path, threshold: float = 0.35):
        self.threshold = threshold
        self.session = ort.InferenceSession(str(model_path), providers=['CPUExecutionProvider'])
        
        input_cfg = self.session.get_inputs()[0]
        self.input_name = input_cfg.name
        self.target_size = input_cfg.shape[1] if len(input_cfg.shape) == 4 else 448

        self.tags: List[str] = []
        with open(tags_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    self.tags.append(row[1])

    def preprocess(self, image_path: str) -> np.ndarray:
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"无法读取图片文件: {image_path}")
        
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, _ = img.shape
        max_dim = max(h, w)

        pad_img = np.zeros((max_dim, max_dim, 3), dtype=np.uint8)
        pad_img[:h, :w] = img

        resized = cv2.resize(pad_img, (self.target_size, self.target_size), interpolation=cv2.INTER_AREA)
        formatted = resized.astype(np.float32)
        return np.expand_dims(formatted, axis=0)

    def check_pass(self, image_path: str, whitelist: Set[str], must_reject: Set[str]) -> Tuple[bool, str]:
        try:
            tensor = self.preprocess(image_path)
            raw_outputs = self.session.run(None, {self.input_name: tensor})
            
            # 使用 cast 强制转换类型，消除 SparseTensor 提示
            outputs = cast(np.ndarray, raw_outputs[0])[0]

            detected_tags = {
                self.tags[i] for i, prob in enumerate(outputs)
                if prob >= self.threshold and i < len(self.tags)
            }

            hit_reject = detected_tags.intersection(must_reject)
            if hit_reject:
                reject_list = ", ".join(hit_reject)
                return False, f"命中黑名单标签: [{reject_list}]"

            if whitelist:
                hit_white = detected_tags.intersection(whitelist)
                if not hit_white:
                    return False, f"未匹配到白名单中的主体标签 (识别到的标签: {list(detected_tags)[:5]}...)"

            return True, "匹配成功，无违规标签"

        except Exception as e:
            logger.error(f"[Twitanime] ❌ WD14 推理过程出错: {e}")
            return False, f"WD14 处理失败: {str(e)}"