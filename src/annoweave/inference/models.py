from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from .backends import create_backend
from .config import ConfigError, ModelConfig
from .datatypes import Classification, Detection, Keypoint, ModelResult, TaskType
from .skeletons import guess_format


class BaseModel(ABC):
    """模型基类。子类通过 `task_type` 声明任务类型，并实现 `predict`。

    采用 X-AnyLabeling 的“模型类自述任务类型 + 输出统一归一化”设计，
    上层节点只依赖 `ModelResult`，与具体是 YOLO/RT-DETR/分割/关键点模型无关。

    `predict(image, boxes=None)`：整图模型忽略 `boxes`；自顶向下（top_down）模型
    需要上游给出人框，此时 `boxes` 是 `[(x1, y1, x2, y2), ...]` 的列表。
    """

    task_type = TaskType.DETECTION
    #: True 表示该模型必须由上游提供框（自顶向下姿态），不能直接作用于整图。
    top_down = False

    def __init__(self, model_config: ModelConfig) -> None:
        self.config = model_config
        self._backend = None

    @property
    def backend(self):
        if self._backend is None:
            self._backend = create_backend(self.config)
        return self._backend

    @abstractmethod
    def predict(self, image: np.ndarray, boxes=None) -> ModelResult:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# 注册表：type 字符串 -> 模型类。新增模型类型 = 注册一个新类，不改上层逻辑。
# --------------------------------------------------------------------------- #
REGISTRY: dict[str, type[BaseModel]] = {}


def register(type_name: str):
    def decorator(cls: type[BaseModel]):
        REGISTRY[type_name] = cls
        return cls
    return decorator


def create_model(model_config: ModelConfig) -> BaseModel:
    cls = REGISTRY.get(model_config.model_type)
    if cls is None:
        raise ConfigError(
            f"未知模型类型 {model_config.model_type!r}，可选: {sorted(REGISTRY)}"
        )
    return cls(model_config)


# --------------------------------------------------------------------------- #
# 预处理
# --------------------------------------------------------------------------- #
def letterbox(image, target_w: int, target_h: int, color=(114, 114, 114)):
    """保持长宽比缩放并填充到目标尺寸，返回 (img, ratio, pad_w, pad_h)。

    ratio 为缩放系数；pad_w/pad_h 为左右/上下填充的像素数，用于把预测框映射回原图。
    """
    import cv2  # noqa: PLC0415

    height, width = image.shape[:2]
    ratio = min(target_w / width, target_h / height)
    new_w = int(round(width * ratio))
    new_h = int(round(height * ratio))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_w = (target_w - new_w) // 2
    pad_h = (target_h - new_h) // 2
    top, bottom = pad_h, target_h - new_h - pad_h
    left, right = pad_w, target_w - new_w - pad_w
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color
    )
    return padded, ratio, pad_w, pad_h


def _preprocess(image, config: ModelConfig):
    """把 BGR 帧预处理为模型输入张量。

    返回 (tensor(1, C, H, W) float32 RGB, ratio, pad_w, pad_h)。
    分类模型默认直接 resize（letterbox=False）；检测默认 letterbox。
    """
    import cv2  # noqa: PLC0415

    target_w, target_h = config.input_size
    if config.letterbox:
        img, ratio, pad_w, pad_h = letterbox(image, target_w, target_h)
    else:
        img = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        ratio, pad_w, pad_h = 1.0, 0.0, 0.0
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    tensor = np.transpose(rgb, (2, 0, 1))[None, ...]  # (1, C, H, W)
    return tensor, ratio, pad_w, pad_h


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - np.max(values)
    exp_values = np.exp(shifted)
    return exp_values / np.sum(exp_values)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """纯 numpy 的 NMS，boxes 为 (N,4) 的 [x1,y1,x2,y2]，返回保留索引。"""
    if not len(boxes):
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    order = np.argsort(scores)[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[order[1:]] - inter
        invalid = union <= 0
        iou = np.where(invalid, np.zeros_like(union), inter / np.where(invalid, 1, union))
        order = order[1:][iou <= iou_threshold]
    return keep


# --------------------------------------------------------------------------- #
# 检测输出解析
# --------------------------------------------------------------------------- #
def parse_yolo_detection(
    output: Any,
    config: ModelConfig,
    ratio: float = 1.0,
    pad_w: float = 0.0,
    pad_h: float = 0.0,
) -> ModelResult:
    """解析 YOLO 风格检测输出，返回按原图坐标的 ModelResult。

    采用与参考 onnx_infer.py 一致的约定：
    - 列数 == 4 + C：YOLOv8 风格，score = max(类别分)，类别从第 4 列起。
    - 列数 == 5 + C：YOLOv5 风格（含 objectness），score = objectness × max(类别分)，
      类别从第 5 列起（objectness 在第 4 列），兼容常见 YOLOv5 导出。
    """
    num_classes = len(config.classes)
    if num_classes == 0:
        raise ConfigError(f"检测模型 {config.name} 未配置类别表")
    arr = np.asarray(output)
    feature_range = range(num_classes + 4, num_classes + 6)
    if arr.ndim == 3:
        # 判定是否为 (B, 特征列, N)：第 1 维是特征列数、第 2 维是锚点数。
        if arr.shape[1] in feature_range and arr.shape[2] not in feature_range:
            arr = arr.transpose(0, 2, 1)
        arr = arr[0]
    elif arr.ndim == 1:
        arr = arr[None, :]
    if arr.ndim != 2:
        raise ConfigError(f"检测输出维度非法: {arr.shape}")

    cols = arr.shape[1]
    if cols == num_classes + 5:
        has_obj = True
        cls_start = 5
    elif cols == num_classes + 4:
        has_obj = False
        cls_start = 4
    else:
        raise ConfigError(
            f"检测输出列数 {cols} 与类别数 {num_classes} 不匹配 (应为 4+C 或 5+C)"
        )

    xyxy_layout = config.output_format != "yolo"
    detections: list[Detection] = []
    for row in arr:
        cls_scores = row[cls_start : cls_start + num_classes]
        cls_id = int(np.argmax(cls_scores))
        conf = float(cls_scores[cls_id])
        if has_obj:
            conf *= float(row[4])
        if conf < config.score_threshold or conf <= 0:
            continue
        if xyxy_layout:
            x1, y1, x2, y2 = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        else:
            cx, cy, w, h = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            x1, y1 = cx - w / 2, cy - h / 2
            x2, y2 = cx + w / 2, cy + h / 2
        # 映射回原图坐标
        x1 = (x1 - pad_w) / ratio
        y1 = (y1 - pad_h) / ratio
        x2 = (x2 - pad_w) / ratio
        y2 = (y2 - pad_h) / ratio
        detections.append(
            Detection(x1, y1, x2, y2, config.classes[cls_id], conf)
        )
    if not detections:
        return ModelResult(task_type=TaskType.DETECTION, detections=[])

    boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in detections])
    scores = np.array([d.score for d in detections])
    keep = _nms(boxes, scores, config.iou_threshold)[: config.max_det]
    kept = [detections[i] for i in keep]
    return ModelResult(task_type=TaskType.DETECTION, detections=kept)


# --------------------------------------------------------------------------- #
# 分类输出解析
# --------------------------------------------------------------------------- #
def parse_classification(output: Any, config: ModelConfig) -> ModelResult:
    """解析分类输出 `[1, C]`（或 `[N, C]`），返回分类结果。"""
    num_classes = len(config.classes)
    if num_classes == 0:
        raise ConfigError(f"分类模型 {config.name} 未配置类别表")
    arr = np.asarray(output).reshape(-1)
    if arr.size < num_classes:
        raise ConfigError(
            f"分类模型 {config.name} 输出长度 {arr.size} 小于类别数 {num_classes}"
        )
    vec = arr[:num_classes].astype(np.float32)
    if config.output_format == "logits":
        probs = _softmax(vec)
    else:
        probs = vec
    # 归一化（若已 softmax 或为概率则保持）
    total = float(probs.sum())
    if total > 0 and abs(total - 1.0) > 1e-4:
        probs = probs / total
    cls_id = int(np.argmax(probs))
    return ModelResult(
        task_type=TaskType.CLASSIFICATION,
        classification=Classification(config.classes[cls_id], float(probs[cls_id])),
    )


# --------------------------------------------------------------------------- #
# 姿态输出解析
# --------------------------------------------------------------------------- #
def _pose_feature_layout(cols: int, config: ModelConfig) -> tuple[int, int, int, bool, int]:
    """推断姿态输出的列布局。

    返回 `(关键点数 K, 类别起始列 cls_start, 类别数 nc, 是否有 objectness, 每个点的列数)`。

    支持的常见 ONNX 导出布局：
    - YOLOv8/v11-pose（Ultralytics）：`4 + nc + 3K`，conf = 各类别分最大值；
    - YOLOv5-pose：`5 + nc + 3K`，conf = objectness × 类别分；
    - 只有单个置信度列：`5 + 3K`；
    - 置信度 + 类别下标：`6 + 3K`；
    - 部分轻量导出省略关键点置信度：`K` 个点各 2 列（score 记为 1.0）。
    """
    nk = int(getattr(config, "num_keypoints", 0) or 0)
    nc = max(1, len(config.classes))
    #: (表头列数, 类别起始列, 类别数, 是否有 objectness)
    layouts = (
        (4 + nc, 4, nc, False),   # YOLOv8/v11-pose：4 框 + nc 类
        (5 + nc, 5, nc, True),    # YOLOv5-pose：4 框 + objectness + nc 类
        (5, 4, 0, False),         # 只有单列置信度
        (6, 4, 0, False),         # 置信度 + 类别下标
    )
    if nk > 0:
        for stride in (3, 2):
            for header, cls_start, classes, has_obj in layouts:
                if cols - stride * nk == header:
                    return nk, cls_start, classes, has_obj, stride
        raise ConfigError(
            f"姿态模型 {config.name} 输出列数 {cols} 与声明的关键点数 {nk} 不匹配"
        )

    for stride in (3, 2):
        for header, cls_start, classes, has_obj in layouts:
            rest = cols - header
            if rest > 0 and rest % stride == 0:
                return rest // stride, cls_start, classes, has_obj, stride
    raise ConfigError(
        f"无法从输出列数 {cols} 推断姿态关键点布局；请在模型里填写关键点数量"
    )


def parse_yolo_pose(
    output: Any,
    config: ModelConfig,
    ratio: float = 1.0,
    pad_w: float = 0.0,
    pad_h: float = 0.0,
) -> ModelResult:
    """解析 YOLO 风格的一次性（整图）姿态输出。"""
    arr = np.asarray(output)
    if arr.ndim == 3:
        arr = arr[0]
    elif arr.ndim == 1:
        arr = arr[None, :]
    if arr.ndim != 2:
        raise ConfigError(f"姿态输出维度非法: {arr.shape}")

    cols = arr.shape[1]
    # (N, C) 还是 (C, N)？按第一维是否为合理特征列数判断。
    try:
        _pose_feature_layout(int(arr.shape[0]), config)
        if int(arr.shape[0]) != cols:
            arr = arr.transpose(1, 0)
    except ConfigError:
        pass

    cols = arr.shape[1]
    k, cls_start, nc, has_obj, stride = _pose_feature_layout(cols, config)
    xyxy_layout = config.output_format == "xyxy"

    detections: list[Detection] = []
    for row in arr:
        if not np.all(np.isfinite(row[: 4 + max(nc, 1)])):
            continue
        if has_obj:
            cls_scores = row[cls_start : cls_start + nc] if nc else row[4:5]
            cls_id = int(np.argmax(cls_scores)) if nc else 0
            # objectness 在第 4 列；无类别表时它就是唯一的置信度列。
            conf = float(cls_scores[cls_id]) * float(row[4]) if nc else float(row[4])
        elif nc:
            cls_scores = row[cls_start : cls_start + nc]
            cls_id = int(np.argmax(cls_scores))
            conf = float(cls_scores[cls_id])
        else:
            cls_id, conf = 0, float(row[4])
        if conf < config.score_threshold or conf <= 0:
            continue

        if xyxy_layout:
            x1, y1, x2, y2 = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        else:
            cx, cy, w, h = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        x1 = (x1 - pad_w) / ratio
        y1 = (y1 - pad_h) / ratio
        x2 = (x2 - pad_w) / ratio
        y2 = (y2 - pad_h) / ratio

        label = config.classes[cls_id] if 0 <= cls_id < len(config.classes) else (
            config.classes[0] if config.classes else "person"
        )
        keypoints: list[Keypoint] = []
        base = cls_start + (nc if nc else 1)
        for index in range(k):
            offset = base + index * stride
            if offset + 1 >= cols:
                break
            kx = (float(row[offset]) - pad_w) / ratio
            ky = (float(row[offset + 1]) - pad_h) / ratio
            kscore = float(row[offset + 2]) if stride == 3 else 1.0
            keypoints.append(Keypoint(kx, ky, kscore))
        detections.append(
            Detection(x1, y1, x2, y2, label, conf, keypoints=keypoints,
                      keypoint_format=_pose_format(config, k))
        )

    if not detections:
        return ModelResult(task_type=TaskType.POSE, detections=[])

    boxes = np.array([[d.x1, d.y1, d.x2, d.y2] for d in detections])
    scores = np.array([d.score for d in detections])
    keep = _nms(boxes, scores, config.iou_threshold)[: config.max_det]
    return ModelResult(task_type=TaskType.POSE, detections=[detections[i] for i in keep])


def _pose_format(config: ModelConfig, count: int) -> str:
    declared = str(getattr(config, "keypoint_format", "") or "")
    if declared:
        return declared
    return guess_format(count)


class _BodyPosePreprocess:
    """DWPose/RTMPose 的自顶向下预处理（bbox → 仿射变换 → 归一化）。"""

    def __init__(self, input_shape, mean, std, padding: float = 1.25):
        self.input_shape = input_shape  # (1, 3, H, W)
        self.mean = np.array(mean, dtype=np.float32)
        self.std = np.array(std, dtype=np.float32)
        self.padding = float(padding)

    def bbox_xyxy2cs(self, bbox: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        center = np.array([(x1 + x2) * 0.5, (y1 + y2) * 0.5], dtype=np.float32)
        scale = np.array([(x2 - x1) * self.padding, (y2 - y1) * self.padding], dtype=np.float32)
        return center, scale

    @staticmethod
    def _rotate_point(pt, angle_rad):
        sn, cs = np.sin(angle_rad), np.cos(angle_rad)
        return np.array([[cs, -sn], [sn, cs]]) @ pt

    @staticmethod
    def _get_3rd_point(a, b):
        direction = a - b
        return b + np.array([-direction[1], direction[0]])

    def warp_matrix(self, center, scale, rot, output_size) -> np.ndarray:
        import cv2  # noqa: PLC0415

        src_w = scale[0]
        dst_w, dst_h = output_size
        rot_rad = np.pi * rot / 180.0
        src_dir = self._rotate_point(np.array([0.0, src_w * -0.5]), rot_rad)
        dst_dir = np.array([0.0, dst_w * -0.5])
        src = np.zeros((3, 2), dtype=np.float32)
        dst = np.zeros((3, 2), dtype=np.float32)
        src[0, :] = center
        src[1, :] = center + src_dir
        dst[0, :] = [dst_w * 0.5, dst_h * 0.5]
        dst[1, :] = np.array([dst_w * 0.5, dst_h * 0.5]) + dst_dir
        src[2, :] = self._get_3rd_point(src[0, :], src[1, :])
        dst[2, :] = self._get_3rd_point(dst[0, :], dst[1, :])
        return cv2.getAffineTransform(np.float32(src), np.float32(dst))

    def __call__(self, image, bbox) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        import cv2  # noqa: PLC0415

        height, width = self.input_shape[-2:]
        center, scale = self.bbox_xyxy2cs(np.asarray(bbox, dtype=np.float32))
        warp = self.warp_matrix(center, scale, 0.0, (width, height))
        warped = cv2.warpAffine(image, warp, (width, height), flags=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(warped, cv2.COLOR_BGR2RGB).astype(np.float32)
        rgb = (rgb - self.mean) / self.std
        tensor = np.transpose(rgb, (2, 0, 1))[None, ...]
        return tensor.astype(np.float32), center, scale


def parse_simcc_pose(
    outputs: Any,
    config: ModelConfig,
    center: np.ndarray,
    scale: np.ndarray,
    image_size: tuple[int, int],
    simcc_split_ratio: float = 2.0,
) -> list[Keypoint]:
    """解析 SimCC 输出（DWPose / RTMPose）：两个 (1, K, L) 张量 → 原图关键点。"""
    if not isinstance(outputs, (list, tuple)) or len(outputs) < 2:
        raise ConfigError(f"SimCC 姿态模型 {config.name} 需要两个输出（simcc_x / simcc_y）")
    simcc_x = np.asarray(outputs[0])
    simcc_y = np.asarray(outputs[1])
    if simcc_x.ndim == 3:
        simcc_x = simcc_x[0]
    if simcc_y.ndim == 3:
        simcc_y = simcc_y[0]
    k = simcc_x.shape[0]
    x_locs = np.argmax(simcc_x, axis=1).astype(np.float32)
    y_locs = np.argmax(simcc_y, axis=1).astype(np.float32)
    max_x = np.amax(simcc_x, axis=1)
    max_y = np.amax(simcc_y, axis=1)
    scores = 0.5 * (max_x + max_y)

    width, height = image_size  # 模型输入 (W, H)
    keypoints: list[Keypoint] = []
    for index in range(k):
        score = float(scores[index])
        x = x_locs[index] / simcc_split_ratio
        y = y_locs[index] / simcc_split_ratio
        x = x / width * float(scale[0]) + float(center[0]) - float(scale[0]) / 2.0
        y = y / height * float(scale[1]) + float(center[1]) - float(scale[1]) / 2.0
        keypoints.append(Keypoint(float(x), float(y), score))
    return keypoints


# --------------------------------------------------------------------------- #
# 内置模型实现
# --------------------------------------------------------------------------- #
@register("yolov8")
class YOLOv8(BaseModel):
    task_type = TaskType.DETECTION

    def predict(self, image: np.ndarray, boxes=None) -> ModelResult:
        tensor, ratio, pad_w, pad_h = _preprocess(image, self.config)
        output = self.backend.run(tensor)
        return parse_yolo_detection(output, self.config, ratio, pad_w, pad_h)


@register("yolov8_cls")
class YOLOv8Cls(BaseModel):
    task_type = TaskType.CLASSIFICATION

    def predict(self, image: np.ndarray, boxes=None) -> ModelResult:
        tensor, _ratio, _pad_w, _pad_h = _preprocess(image, self.config)
        output = self.backend.run(tensor)
        return parse_classification(output, self.config)


@register("yolov8_pose")
class YOLOv8Pose(BaseModel):
    """一次性（整图）姿态模型：YOLOv8-pose / YOLOv11-pose 的 ONNX 导出。

    输出同时包含人框和该人的全部关键点，因此**不需要**上游先给框。
    """

    task_type = TaskType.POSE

    def predict(self, image: np.ndarray, boxes=None) -> ModelResult:
        tensor, ratio, pad_w, pad_h = _preprocess(image, self.config)
        output = self.backend.run(tensor)
        return parse_yolo_pose(output, self.config, ratio, pad_w, pad_h)


@register("dwpose")
class DWPose(BaseModel):
    """自顶向下姿态模型：DWPose / RTMPose 的 SimCC ONNX 导出。

    输入是"人框 + 图"，所以必须由上游（裁剪姿态节点 / 姿态推理节点的人框）提供框。
    输入尺寸请按权重填写（DWPose 常用 192×256，即宽 192、高 256）。
    """

    task_type = TaskType.POSE
    top_down = True

    def __init__(self, model_config: ModelConfig) -> None:
        super().__init__(model_config)
        width, height = model_config.input_size
        self._pre = _BodyPosePreprocess(
            (1, 3, height, width),
            mean=(123.675, 116.28, 103.53),
            std=(58.395, 57.12, 57.375),
        )

    def forward(self, image: np.ndarray, box) -> list[Keypoint]:
        tensor, center, scale = self._pre(image, box)
        output = self.backend.run(tensor)
        width, height = self.config.input_size
        return parse_simcc_pose(output, self.config, center, scale, (width, height))

    def predict(self, image: np.ndarray, boxes=None) -> ModelResult:
        if boxes is None:
            raise ConfigError(
                f"姿态模型 {self.config.name} 是自顶向下模型，需要上游先给出人框"
            )
        label = self.config.classes[0] if self.config.classes else "person"
        detections: list[Detection] = []
        for box in boxes:
            x1, y1, x2, y2 = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
            keypoints = self.forward(image, (x1, y1, x2, y2))
            detections.append(
                Detection(x1, y1, x2, y2, label, 1.0, keypoints=keypoints,
                          keypoint_format=_pose_format(self.config, len(keypoints)))
            )
        return ModelResult(task_type=TaskType.POSE, detections=detections)


# 常用别名：YOLOv5 输出带 objectness（5+C）。
# 解析器按列数自动识别有无 objectness，因此同一检测/分类/姿态后处理即可覆盖。
REGISTRY["yolov5"] = YOLOv8
REGISTRY["yolov5_cls"] = YOLOv8Cls
REGISTRY["yolov5_pose"] = YOLOv8Pose
REGISTRY["rtmpose"] = DWPose


def supports_boxes(model: Any) -> bool:
    """判断某个模型实例是否接受 `boxes` 参数（自顶向下姿态）。"""
    return bool(getattr(model, "top_down", False))


def predict_with_boxes(model: Any, image: np.ndarray, boxes):
    """统一的调用入口：自顶向下模型传框，整图模型忽略框。"""
    if supports_boxes(model):
        return model.predict(image, boxes=boxes)
    return model.predict(image)
