from typing import Any, Dict, Sequence

import numpy as np

from ...types import PythonPostProcessor


def _reshape_output(feat: np.ndarray, anchor_per_layer_count: int, num_classes: int):
    return feat.reshape(
        feat.shape[0],  # batch
        anchor_per_layer_count,
        num_classes + 5,  # boundingbox(4) + objectness score + classes score of that object
        feat.shape[2],  # the number of width grid
        feat.shape[3],  # the number of height grid
    ).transpose(0, 1, 3, 4, 2)


def _nms(box_scores: np.ndarray, iou_threshold: float = 0.45) -> np.ndarray:
    scores = box_scores[:, 4]
    boxes = box_scores[:, :4]
    picked = []
    indexes = np.argsort(scores)[::-1]
    while len(indexes) > 0:
        current = indexes[0]
        picked.append(current.item())
        if len(indexes) == 1:
            break
        current_box = boxes[current, :]
        indexes = indexes[1:]
        rest_boxes = boxes[indexes, :]
        iou = _box_iou(rest_boxes, np.expand_dims(current_box, axis=0))
        indexes = indexes[iou <= iou_threshold]
    return picked


def _box_area(left_top: np.ndarray, right_bottom: np.ndarray):
    """Compute the areas of rectangles given two corners."""
    # https://github.com/mlcommons/inference/blob/de6497f9d64b85668f2ab9c26c9e3889a7be257b/vision/classification_and_detection/python/models/utils.py#L89-L100
    width_height = np.clip(right_bottom - left_top, a_min=0.0, a_max=None)
    return width_height[..., 0] * width_height[..., 1]


def _box_iou(boxes1: np.ndarray, boxes2: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    """Return intersection-over-union (Jaccard index) of boxes."""
    # https://github.com/mlcommons/inference/blob/de6497f9d64b85668f2ab9c26c9e3889a7be257b/vision/classification_and_detection/python/models/utils.py#L103-L119
    overlap_left_top = np.maximum(boxes1[..., :2], boxes2[..., :2])
    overlap_right_bottom = np.minimum(boxes1[..., 2:], boxes2[..., 2:])
    overlap_area = _box_area(overlap_left_top, overlap_right_bottom)
    area1 = _box_area(boxes1[..., :2], boxes1[..., 2:])
    area2 = _box_area(boxes2[..., :2], boxes2[..., 2:])
    return overlap_area / (area1 + area2 - overlap_area + eps)


class YOLOv5PythonPostProcessor(PythonPostProcessor):
    def __init__(self, anchors, class_names, input_shape=(640, 640)):
        self.anchors = anchors
        self.class_names = class_names
        self.input_shape = input_shape
        self.num_layers = anchors.shape[0]
        self.anchor_per_layer_count = anchors.shape[1]
        self.stride = np.array(
            [8.0 * pow(2, i) for i in range(self.num_layers)],
            dtype=np.float32,
        )
        self.grid, self.anchor_grid = self.init_grid()

    def __call__(
        self,
        model_outputs: Sequence[np.ndarray],
        contexts: Sequence[Dict[str, Any]],
        conf_thres: float = 0.25,
        iou_thres: float = 0.45,
        with_sigmoid: bool = False,
    ):
        """Convert the outputs of this model to a list of bounding boxes, scores and labels

        Args:
            model_outputs: P3/8, P4/16, P5/32 features from yolov5l model.
                To learn more about the outputs of preprocess (i.e., model inputs),
                please refer to [YOLOv5l Outputs](yolov5l.md#outputs) or
                [YOLOv5m Outputs](yolov5m.md#outputs).
            contexts: A configuration for each image generated by the preprocessor.
                For example, it could be the reduction ratio of the image, the actual image width
                and height.
            conf_thres: Confidence score threshold. The default to 0.25
            iou_thres: IoU threshold value for the NMS processing. The default to 0.45.
            with_sigmoid: Whether to apply sigmoid function to the model outputs. The default to
                False.

        Returns:
            Detected Bounding Box and its score and label represented as `ObjectDetectionResult`.
                The details of `ObjectDetectionResult` can be found below.

        Definition of ObjectDetectionResult and LtrbBoundingBox:
            ::: furiosa.models.vision.postprocess.LtrbBoundingBox
                options:
                    show_source: true
            ::: furiosa.models.vision.postprocess.ObjectDetectionResult
                options:
                    show_source: true
        """
        outputs = []
        model_outputs = [
            _reshape_output(f, self.anchor_per_layer_count, len(self.class_names))
            for f in model_outputs
        ]
        for model_output, grid, stride, anchor_grid in zip(
            model_outputs, self.grid, self.stride, self.anchor_grid
        ):
            _, _, nx, ny, _ = model_output.shape
            if with_sigmoid:
                model_output = sigmoid(model_output)
            xy, wh, conf = np.split(model_output, [2, 4], axis=4)
            xy = (xy * 2 + grid) * stride
            wh = (wh * 2) ** 2 * anchor_grid
            y = np.concatenate((xy, wh, conf), axis=4)
            outputs.append(
                np.reshape(
                    y,
                    (
                        1,
                        self.anchor_per_layer_count * nx * ny,
                        len(self.class_names) + 5,
                    ),
                )
            )
        outputs = np.concatenate(outputs, axis=1)
        model_outputs = non_max_suppression(outputs, conf_thres, iou_thres)

        for i, prediction in enumerate(model_outputs):
            ratio, dwdh = contexts[i]["scale"], contexts[i]["pad"]
            prediction[:, [0, 2]] = (1 / ratio) * (prediction[:, [0, 2]] - dwdh[0])
            prediction[:, [1, 3]] = (1 / ratio) * (prediction[:, [1, 3]] - dwdh[1])

        return model_outputs

    def init_grid(self):
        grid = [np.zeros(1)] * self.num_layers
        anchor_grid = [np.zeros(1)] * self.num_layers

        nx_ny = [
            (
                int(self.input_shape[0] / (8 * pow(2, i))),
                int(self.input_shape[1] / (8 * pow(2, i))),
            )
            for i in range(self.num_layers)
        ]
        for i in range(self.num_layers):
            grid[i], anchor_grid[i] = self.make_grid(nx_ny[i][0], nx_ny[i][1], i)

        return grid, anchor_grid

    def make_grid(self, nx: int, ny: int, i: int):
        shape = 1, self.anchor_per_layer_count, ny, nx, 2
        y, x = np.arange(ny, dtype=np.float32), np.arange(nx, dtype=np.float32)
        yv, xv = np.meshgrid(y, x, indexing="ij")
        grid = np.broadcast_to(np.stack((xv, yv), axis=2), shape) - 0.5
        anchor_grid = np.broadcast_to(
            np.reshape(
                self.anchors[i] * self.stride[i],
                (1, self.anchor_per_layer_count, 1, 1, 2),
            ),
            shape,
        )
        return grid, anchor_grid


def sigmoid(x: np.ndarray) -> np.ndarray:
    # pylint: disable=invalid-name
    return 1 / (1 + np.exp(-x))


# https://github.com/ultralytics/yolov5/blob/v7.0/utils/general.py#L884-L999
def non_max_suppression(
    prediction: np.ndarray,
    conf_thres: float,
    iou_thres: float,
):
    # pylint: disable=invalid-name,too-many-locals

    batch_size = prediction.shape[0]
    candidates = prediction[..., 4] > conf_thres
    assert 0 <= conf_thres <= 1, conf_thres
    assert 0 <= iou_thres <= 1, iou_thres

    _max_wh = 7680  # (pixels) maximum box width and height
    max_nms = 30000

    output = [np.empty((0, 6))] * batch_size
    for xi, x in enumerate(prediction):
        x = x[candidates[xi]]
        if not x.shape[0]:
            continue

        x[:, 5:] *= x[:, 4:5]  # conf = obj_conf * cls_conf

        box = xywh2xyxy(x[:, :4])

        i, j = np.where(x[:, 5:] > conf_thres)
        x = np.concatenate(
            (
                box[i],
                x[i, j + 5, np.newaxis].astype(np.float32),
                j[:, np.newaxis].astype(np.float32),
            ),
            axis=1,
        )

        n = x.shape[0]
        if not n:
            continue

        if n > max_nms:
            x = x[np.argsort(x[:, 4])[::-1][:max_nms]]

        i = _nms(x[:, :5])

        output[xi] = x[i]

    return output


# https://github.com/ultralytics/yolov5/blob/v7.0/utils/general.py#L760-L767
def xywh2xyxy(x: np.ndarray) -> np.ndarray:
    # pylint: disable=invalid-name
    y = np.copy(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y
