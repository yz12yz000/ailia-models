import sys
import time

import cv2
import numpy as np

import ailia
import facemesh_utils as fut

sys.path.append('../../util')
from utils import get_base_parser, update_parser, get_savepath  # noqa: E402
from webcamera_utils import get_capture, get_writer  # noqa: E402
from image_utils import load_image  # noqa: E402
from model_utils import check_and_download_models  # noqa: E402

# logger
from logging import getLogger   # noqa: E402
logger = getLogger(__name__)


# ======================
# Parameters 1
# ======================
IMAGE_PATH = 'man.jpg'
SAVE_IMAGE_PATH = 'output.png'
IMAGE_HEIGHT = 128
IMAGE_WIDTH = 128


# ======================
# Argument Parser Config
# ======================
parser = get_base_parser(
    'Face Mesh, an on-device real-time face recognition.',
    IMAGE_PATH,
    SAVE_IMAGE_PATH,
)
parser.add_argument(
    '-n', '--normal',
    action='store_true',
    help='By default, the optimized model is used, but with this option, ' +
    'you can switch to the normal (not optimized) model'
)
args = update_parser(parser)


# ======================
# Parameters 2
# ======================
DETECTION_MODEL_NAME = 'blazeface'
LANDMARK_MODEL_NAME = 'facemesh'
if args.normal:
    DETECTION_WEIGHT_PATH = f'{DETECTION_MODEL_NAME}.onnx'
    DETECTION_MODEL_PATH = f'{DETECTION_MODEL_NAME}.onnx.prototxt'
    LANDMARK_WEIGHT_PATH = f'{LANDMARK_MODEL_NAME}.onnx'
    LANDMARK_MODEL_PATH = f'{LANDMARK_MODEL_NAME}.onnx.prototxt'
else:
    DETECTION_WEIGHT_PATH = f'{DETECTION_MODEL_NAME}.opt.onnx'
    DETECTION_MODEL_PATH = f'{DETECTION_MODEL_NAME}.opt.onnx.prototxt'
    LANDMARK_WEIGHT_PATH = f'{LANDMARK_MODEL_NAME}.opt.onnx'
    LANDMARK_MODEL_PATH = f'{LANDMARK_MODEL_NAME}.opt.onnx.prototxt'
DETECTION_REMOTE_PATH = f'https://storage.googleapis.com/ailia-models/{DETECTION_MODEL_NAME}/'
LANDMARK_REMOTE_PATH = f'https://storage.googleapis.com/ailia-models/{LANDMARK_MODEL_NAME}/'


# ======================
# Utils
# ======================
def draw_roi(img, roi):
    if len(roi) <= 0:
        return
    for i in range(roi.shape[0]):
        (x1, x2, x3, x4), (y1, y2, y3, y4) = roi[i]
        cv2.line(img, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 0), 2)
        cv2.line(img, (int(x1), int(y1)), (int(x3), int(y3)), (0, 255, 0), 2)
        cv2.line(img, (int(x2), int(y2)), (int(x4), int(y4)), (0, 0, 0), 2)
        cv2.line(img, (int(x3), int(y3)), (int(x4), int(y4)), (0, 0, 0), 2)


def draw_landmarks(img, points, color=(0, 0, 255), size=2):
    for point in points:
        x, y = point
        x, y = int(x), int(y)
        cv2.circle(img, (x, y), size, color, thickness=cv2.FILLED)


def estimate_landmarks(input_data, src_img, scale, pad, detector, fut, estimator):
    # Face detection
    preds = detector.predict([input_data])
    detections = fut.detector_postprocess(preds)

    # Face landmark estimation
    if detections[0].size != 0:
        imgs, affines, box = fut.estimator_preprocess(
            src_img[:, :, ::-1], detections, scale, pad
        )

        dynamic_input_shape = False

        if dynamic_input_shape:
            estimator.set_input_shape(imgs.shape)
            landmarks, confidences = estimator.predict([imgs])
            landmarks = landmarks.reshape((imgs.shape[0],1404))
            confidences = confidences.reshape((imgs.shape[0],1))
            normalized_landmarks = landmarks / 192.0
            landmarks = fut.denormalize_landmarks(
                normalized_landmarks, affines
            )
        else:
            landmarks = np.zeros((imgs.shape[0], 1404))
            confidences = np.zeros((imgs.shape[0], 1))

            for i in range(imgs.shape[0]):
                landmark, confidences[i, :] = estimator.predict([imgs[i:i+1, :, :, :]])
                normalized_landmark = landmark / 192.0

                # postprocessing
                landmarks[i, :] = normalized_landmark
        
            landmarks = fut.denormalize_landmarks(
                landmarks, affines
            )

        return landmarks, confidences, box

    return [], [], []


# ======================
# Main functions
# ======================
def recognize_from_image():
    # net initialize
    detector = ailia.Net(
        DETECTION_MODEL_PATH, DETECTION_WEIGHT_PATH, env_id=args.env_id
    )
    estimator = ailia.Net(
        LANDMARK_MODEL_PATH, LANDMARK_WEIGHT_PATH, env_id=args.env_id
    )

    # input image loop
    for image_path in args.input:
        # prepare input data
        logger.info(image_path)
        src_img = cv2.imread(image_path)
        _, img128, scale, pad = fut.resize_pad(src_img[:, :, ::-1])
        input_data = img128.astype('float32') / 127.5 - 1.0
        input_data = np.expand_dims(np.moveaxis(input_data, -1, 0), 0)

        # inference
        logger.info('Start inference...')
        if args.benchmark:
            logger.info('BENCHMARK mode')
            total_time = 0
            for i in range(args.benchmark_count):
                start = int(round(time.time() * 1000))
                landmarks, confidences, box = estimate_landmarks(input_data, src_img, scale, pad, detector, fut, estimator)
                end = int(round(time.time() * 1000))
                if i != 0:
                    total_time = total_time + (end - start)
                logger.info(f'\tailia processing time {end - start} ms')
            logger.info(f'\taverage time {total_time / (args.benchmark_count-1)} ms')
        else:
            landmarks, confidences, box = estimate_landmarks(input_data, src_img, scale, pad, detector, fut, estimator)
        
        draw_roi(src_img, box)
        for i in range(len(landmarks)):
            landmark, confidence = landmarks[i], confidences[i]
            draw_landmarks(src_img, landmark[:, :2], size=1)

        savepath = get_savepath(args.savepath, image_path)
        logger.info(f'saved at : {savepath}')
        cv2.imwrite(savepath, src_img)
    logger.info('Script finished successfully.')


def recognize_from_video():
    # net initialize
    detector = ailia.Net(
        DETECTION_MODEL_PATH, DETECTION_WEIGHT_PATH, env_id=args.env_id
    )
    estimator = ailia.Net(
        LANDMARK_MODEL_PATH, LANDMARK_WEIGHT_PATH, env_id=args.env_id
    )

    capture = get_capture(args.video)

    # create video writer if savepath is specified as video format
    if args.savepath != SAVE_IMAGE_PATH:
        f_h = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        f_w = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        writer = get_writer(args.savepath, f_h, f_w)
    else:
        writer = None

    frame_shown = False
    while(True):
        ret, frame = capture.read()
        if (cv2.waitKey(1) & 0xFF == ord('q')) or not ret:
            break
        if frame_shown and cv2.getWindowProperty('frame', cv2.WND_PROP_VISIBLE) == 0:
            break

        _, img128, scale, pad = fut.resize_pad(frame[:,:,::-1])
        input_data = img128.astype('float32') / 127.5 - 1.0
        input_data = np.expand_dims(np.moveaxis(input_data, -1, 0), 0)

        landmarks, confidences, box = estimate_landmarks(input_data, frame, scale, pad, detector, fut, estimator)

        draw_roi(frame, box)

        for i in range(len(landmarks)):
            landmark, confidence = landmarks[i], confidences[i]
            # if confidence > 0:
            # Can be > 1, no idea what it represents
            draw_landmarks(frame, landmark[:, :2], size=1)

        visual_img = frame
        if args.video == '0': # Flip horizontally if camera
            visual_img = np.ascontiguousarray(frame[:,::-1,:])

        cv2.imshow('frame', visual_img)
        frame_shown = True

        # save results
        if writer is not None:
            writer.write(frame)

    capture.release()
    if writer is not None:
        writer.release()
    cv2.destroyAllWindows()
    if writer is not None:
        writer.release()
    logger.info('Script finished successfully.')


def main():
    # model files check and download
    check_and_download_models(
        DETECTION_WEIGHT_PATH, DETECTION_MODEL_PATH, DETECTION_REMOTE_PATH
    )
    check_and_download_models(
        LANDMARK_WEIGHT_PATH, LANDMARK_MODEL_PATH, LANDMARK_REMOTE_PATH
    )

    if args.video is not None:
        # video mode
        recognize_from_video()
    else:
        # image mode
        recognize_from_image()


if __name__ == '__main__':
    main()
