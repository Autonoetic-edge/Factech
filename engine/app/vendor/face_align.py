# Vendored from InsightFace (MIT license):
# https://github.com/deepinsight/insightface/blob/3e6486942a1be2da0e5b475fac375ea73264bd21/python-package/insightface/utils/face_align.py
# (tag v0.7). MIT license: https://github.com/deepinsight/insightface/blob/master/LICENSE

import cv2
import numpy as np

class _SimilarityTransform:
    def __init__(self, scale=None, translation=None, rotation=0):
        if scale is None:
            self.params = np.eye(3)
            return
        scale = float(scale)
        translation = (0.0, 0.0) if translation is None else translation
        rot = float(rotation) * np.pi / 180.0
        cos, sin = np.cos(rot), np.sin(rot)
        self.params = np.array([
            [scale * cos, -scale * sin, translation[0]],
            [scale * sin, scale * cos, translation[1]],
            [0.0, 0.0, 1.0],
        ])

    def estimate(self, src, dst):

        src = np.asarray(src, dtype=np.float64)
        dst = np.asarray(dst, dtype=np.float64)
        xs, ys = src[:, 0], src[:, 1]
        xd, yd = dst[:, 0], dst[:, 1]
        rows = src.shape[0]
        A = np.zeros((rows * 2, 4))
        b = np.zeros(rows * 2)
        A[0::2, 0] = xs
        A[0::2, 1] = -ys
        A[0::2, 2] = 1.0
        A[1::2, 0] = ys
        A[1::2, 1] = xs
        A[1::2, 3] = 1.0
        b[0::2] = xd
        b[1::2] = yd
        params, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        a, bb, tx, ty = params
        self.params = np.array([
            [a, -bb, tx],
            [bb, a, ty],
            [0.0, 0.0, 1.0],
        ])
        return True

    def __add__(self, other):
        out = _SimilarityTransform()
        out.params = self.params @ other.params
        return out

arcface_dst = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32)

def estimate_norm(lmk, image_size=112,mode='arcface'):
    assert lmk.shape == (5, 2)
    assert image_size%112==0 or image_size%128==0
    if image_size%112==0:
        ratio = float(image_size)/112.0
        diff_x = 0
    else:
        ratio = float(image_size)/128.0
        diff_x = 8.0*ratio
    dst = arcface_dst * ratio
    dst[:,0] += diff_x
    tform = _SimilarityTransform()
    tform.estimate(lmk, dst)
    M = tform.params[0:2, :]
    return M

def norm_crop(img, landmark, image_size=112, mode='arcface'):
    M = estimate_norm(landmark, image_size, mode)
    warped = cv2.warpAffine(img, M, (image_size, image_size), borderValue=0.0)
    return warped

def norm_crop2(img, landmark, image_size=112, mode='arcface'):
    M = estimate_norm(landmark, image_size, mode)
    warped = cv2.warpAffine(img, M, (image_size, image_size), borderValue=0.0)
    return warped, M

def square_crop(im, S):
    if im.shape[0] > im.shape[1]:
        height = S
        width = int(float(im.shape[1]) / im.shape[0] * S)
        scale = float(S) / im.shape[0]
    else:
        width = S
        height = int(float(im.shape[0]) / im.shape[1]) * S
        scale = float(S) / im.shape[1]
    resized_im = cv2.resize(im, (width, height))
    det_im = np.zeros((S, S, 3), dtype=np.uint8)
    det_im[:resized_im.shape[0], :resized_im.shape[1], :] = resized_im
    return det_im, scale

def transform(data, center, output_size, scale, rotation):
    scale_ratio = scale
    rot = float(rotation) * np.pi / 180.0

    t1 = _SimilarityTransform(scale=scale_ratio)
    cx = center[0] * scale_ratio
    cy = center[1] * scale_ratio
    t2 = _SimilarityTransform(translation=(-1 * cx, -1 * cy))
    t3 = _SimilarityTransform(rotation=rot)
    t4 = _SimilarityTransform(translation=(output_size / 2,
                                            output_size / 2))
    t = t1 + t2 + t3 + t4
    M = t.params[0:2]
    cropped = cv2.warpAffine(data,
                              M, (output_size, output_size),
                              borderValue=0.0)
    return cropped, M

def trans_points2d(pts, M):
    new_pts = np.zeros(shape=pts.shape, dtype=np.float32)
    for i in range(pts.shape[0]):
        pt = pts[i]
        new_pt = np.array([pt[0], pt[1], 1.], dtype=np.float32)
        new_pt = np.dot(M, new_pt)

        new_pts[i] = new_pt[0:2]

    return new_pts

def trans_points3d(pts, M):
    scale = np.sqrt(M[0][0] * M[0][0] + M[0][1] * M[0][1])

    new_pts = np.zeros(shape=pts.shape, dtype=np.float32)
    for i in range(pts.shape[0]):
        pt = pts[i]
        new_pt = np.array([pt[0], pt[1], 1.], dtype=np.float32)
        new_pt = np.dot(M, new_pt)

        new_pts[i][0:2] = new_pt[0:2]
        new_pts[i][2] = pts[i][2] * scale

    return new_pts

def trans_points(pts, M):
    if pts.shape[1] == 2:
        return trans_points2d(pts, M)
    else:
        return trans_points3d(pts, M)
