import os
from pathlib import Path
from typing import Tuple

import numpy as np
from tqdm import tqdm

from furiosa.models.vision import YOLOv5l
from furiosa.runtime.sync import create_runner

from .test_acc_util import bdd100k

EXPECTED_MAP = 0.2894519996558422
EXPECTED_MAP_RUST = 0.2895171558198357

CONF_THRES = 0.001
IOU_THRES = 0.45


def load_db_from_env_variable() -> Tuple[Path, bdd100k.Yolov5Dataset]:
    MUST_10K_LIMIT = 10000
    databaset_path = Path(os.environ.get('YOLOV5_DATASET_PATH', "./tests/data/bdd100k_val"))

    db = bdd100k.Yolov5Dataset(databaset_path, mode="val", limit=MUST_10K_LIMIT)

    return databaset_path, db


def test_yolov5l_accuracy(benchmark):
    model: YOLOv5l = YOLOv5l(postprocessor_type="Python")

    image_directory, yolov5db = load_db_from_env_variable()

    print(f"dataset_path: {image_directory}")
    metric = bdd100k.MAPMetricYolov5(num_classes=10)

    num_images = len(yolov5db)
    yolov5db = iter(tqdm(yolov5db))

    def read_image():
        im, boxes_target, classes_target = next(yolov5db)
        return (im, boxes_target, classes_target), {}

    def workload(im, boxes_target, classes_target):
        batch_im = [im]

        batch_pre_img, batch_preproc_param = model.preprocess(
            batch_im,
        )  # single-batch
        batch_feat = runner.run(np.expand_dims(batch_pre_img[0], axis=0))
        detected_boxes = model.postprocess(
            batch_feat, batch_preproc_param, conf_thres=CONF_THRES, iou_thres=IOU_THRES
        )
        det_out = bdd100k.to_numpy(detected_boxes[0])
        metric(
            boxes_pred=det_out[:, :4],
            scores_pred=det_out[:, 4],
            classes_pred=det_out[:, 5],
            boxes_target=boxes_target,
            classes_target=classes_target,
        )

    with create_runner(model.model_source()) as runner:
        benchmark.pedantic(workload, setup=read_image, rounds=num_images)

    result = metric.compute()
    print("YOLOv5Large mAP:", result['map'])
    print("YOLOv5Large mAP50:", result['map50'])
    print("YOLOv5Large ap_class:", result['ap_class'])
    print("YOLOv5Large ap50_class:", result['ap50_class'])
    assert result['map'] == EXPECTED_MAP, "Accuracy check failed"


def test_yolov5l_accuracy_with_rust_pp(benchmark):
    model: YOLOv5l = YOLOv5l(postprocessor_type="Rust")

    image_directory, yolov5db = load_db_from_env_variable()

    print(f"dataset_path: {image_directory}")
    metric = bdd100k.MAPMetricYolov5(num_classes=10)

    num_images = len(yolov5db)
    yolov5db = iter(tqdm(yolov5db))

    def read_image():
        im, boxes_target, classes_target = next(yolov5db)
        return (im, boxes_target, classes_target), {}

    def workload(im, boxes_target, classes_target):
        batch_im = [im]

        batch_pre_img, batch_preproc_param = model.preprocess(
            batch_im,
        )  # single-batch
        batch_feat = runner.run(np.expand_dims(batch_pre_img[0], axis=0))
        detected_boxes = model.postprocess(
            batch_feat, batch_preproc_param, conf_thres=CONF_THRES, iou_thres=IOU_THRES
        )
        det_out = bdd100k.to_numpy(detected_boxes[0])
        metric(
            boxes_pred=det_out[:, :4],
            scores_pred=det_out[:, 4],
            classes_pred=det_out[:, 5],
            boxes_target=boxes_target,
            classes_target=classes_target,
        )

    with create_runner(model.model_source()) as runner:
        benchmark.pedantic(workload, setup=read_image, rounds=num_images)

    result = metric.compute()
    print("YOLOv5Large mAP:", result['map'])
    print("YOLOv5Large mAP50:", result['map50'])
    print("YOLOv5Large ap_class:", result['ap_class'])
    print("YOLOv5Large ap50_class:", result['ap50_class'])
    assert result['map'] == EXPECTED_MAP_RUST, "Accuracy check failed"
