"""The robot's head camera as the vision planner sees it: colour and depth from the same RGB-D camera.

The head camera (``robot.py``: an RGB-D bar on the head, 64° vertical field of view, looking down at
the table) is rendered at ``RENDER_SIZE`` (1024 × 768) with MuJoCo's renderer, colour and depth from
the same camera pose. Depth is MuJoCo's depth buffer linearised to metres along the optical axis.
The planner gets ``WINDOW``: columns 256–767 and rows 208–591 of that render (512 × 384), the mat in
front of the bottle at full resolution, i.e. a 2× digital zoom on the work area. Pixel coordinates
(u, v) are in this window: u to the right, v down, (0, 0) the top-left pixel, whose centre is
(0.5, 0.5).

Nothing here reads a pill's pose: a frame is pixels and the camera's calibration (intrinsics from the
field of view and the window, extrinsics from the camera pose at the render).

* ``back_project`` turns a pixel and its depth into a point in the world frame (pinhole model;
  MuJoCo cameras look along their -z with +y up, so image right is +x and image down is -y).
* ``grasp_axis`` and ``finger_obstruction`` are the scripted grasp's only look at the scene, from the
  same frame: the long axis of the raised depth blob under a pointed pixel (a pill is 8 mm tall on the
  mat), and how much of what the depth shows stands where the open fingertips would come down.
* ``with_ticks`` draws pixel ticks on the image borders to help the model read coordinates. It
  overlays nothing about the scene: no ids, no positions.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np

RENDER_SIZE = (1024, 768)  # (width, height) of the head camera render
WINDOW = (256, 208, 512, 384)  # (u0, v0, width, height) of the work-area window sent to the planner
TICK_PX, LABEL_PX = 32, 64  # border ticks every 32 px, labelled every 64 px
JPEG_QUALITY = 92


@dataclass(frozen=True)
class Intrinsics:
    """A pinhole camera: focal lengths and principal point in pixels, in continuous image coordinates
    (pixel (u, v) covers [u, u + 1) x [v, v + 1))."""

    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    @classmethod
    def from_fovy(cls, fovy_deg: float, size: tuple[int, int], window: tuple[int, int, int, int] | None = None):
        """MuJoCo's camera model: `fovy` is the vertical field of view, pixels are square and the principal
        point is the image centre. A window keeps the focal length and shifts the principal point."""
        width, height = size
        f = (height / 2) / math.tan(math.radians(fovy_deg) / 2)
        if window is None:
            return cls(f, f, width / 2, height / 2, width, height)
        u0, v0, w, h = window
        return cls(f, f, width / 2 - u0, height / 2 - v0, w, h)

    def as_dict(self) -> dict:
        return {"fx": round(self.fx, 4), "fy": round(self.fy, 4), "cx": round(self.cx, 4), "cy": round(self.cy, 4),
                "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Frame:
    """One capture: the window's colour and depth, the calibration and the simulated time."""

    rgb: np.ndarray  # (height, width, 3) uint8
    depth: np.ndarray  # (height, width) float32: metres along the optical axis
    intrinsics: Intrinsics
    position: np.ndarray  # camera position in the world frame [m]
    rotation: np.ndarray  # 3x3: the camera's axes in the world frame (world = rotation @ camera)
    t: float = 0.0

    def contains(self, u: int, v: int) -> bool:
        return 0 <= u < self.intrinsics.width and 0 <= v < self.intrinsics.height

    def point(self, u: int, v: int) -> np.ndarray | None:
        """The world point seen at pixel (u, v), or None when (u, v) is outside the window or sees nothing."""
        if not self.contains(u, v):
            return None
        depth = float(self.depth[v, u])
        if not math.isfinite(depth) or depth <= 0.0:
            return None
        return back_project(u, v, depth, self.intrinsics, self.position, self.rotation)


def back_project(u: float, v: float, depth_m: float, intrinsics: Intrinsics, position, rotation) -> np.ndarray:
    """The world point at depth `depth_m` (along the optical axis) behind the centre of pixel (u, v)."""
    k = intrinsics
    x = (u + 0.5 - k.cx) / k.fx * depth_m
    y = -(v + 0.5 - k.cy) / k.fy * depth_m
    return np.asarray(position, dtype=float) + np.asarray(rotation, dtype=float) @ np.array([x, y, -depth_m])


def project(point, intrinsics: Intrinsics, position, rotation) -> tuple[float, float] | None:
    """Continuous image coordinates of a world point (None behind the camera); the pixel is their floor."""
    local = np.asarray(rotation, dtype=float).T @ (np.asarray(point, dtype=float) - np.asarray(position, dtype=float))
    if local[2] >= 0:
        return None
    depth = -float(local[2])
    k = intrinsics
    return k.cx + k.fx * float(local[0]) / depth, k.cy - k.fy * float(local[1]) / depth


def back_project_all(frame: Frame, rows: slice, cols: slice) -> np.ndarray:
    """World points of a block of pixels, shape (rows, cols, 3)."""
    k = frame.intrinsics
    v, u = np.mgrid[rows, cols]
    d = frame.depth[rows, cols].astype(float)
    local = np.stack([(u + 0.5 - k.cx) / k.fx * d, -(v + 0.5 - k.cy) / k.fy * d, -d], axis=-1)
    return np.asarray(frame.position, dtype=float) + local @ np.asarray(frame.rotation, dtype=float).T


def grasp_axis(frame: Frame, u: int, v: int, table_z: float, *, radius_px: int = 18, raised_m: float = 0.002,
               top_m: float = 0.014, min_pixels: int = 12, elongation: float = 2.0) -> float | None:
    """The long axis (yaw in the table plane, radians) of the raised blob under pixel (u, v): the pixels
    within `radius_px` whose depth puts them `raised_m`–`top_m` above the table, connected to (u, v) or to
    a raised pixel within 3 px of it. None when there is no such blob, it is too small, or it has no
    clear long axis (eigenvalue ratio below `elongation`)."""
    from scipy import ndimage

    if not frame.contains(u, v):
        return None
    h, w = frame.depth.shape
    rows = slice(max(0, v - radius_px), min(h, v + radius_px + 1))
    cols = slice(max(0, u - radius_px), min(w, u + radius_px + 1))
    points = back_project_all(frame, rows, cols)
    height = points[..., 2] - table_z
    raised = (height > raised_m) & (height < top_m) & np.isfinite(height)
    labels, count = ndimage.label(raised)
    if count == 0:
        return None
    pu, pv = u - cols.start, v - rows.start
    near = labels[max(0, pv - 3):pv + 4, max(0, pu - 3):pu + 4]
    if labels[pv, pu]:
        chosen = labels[pv, pu]
    elif near.any():
        values, counts = np.unique(near[near > 0], return_counts=True)
        chosen = values[np.argmax(counts)]
    else:
        return None
    xy = points[labels == chosen][:, :2]
    if len(xy) < min_pixels:
        return None
    eigenvalues, eigenvectors = np.linalg.eigh(np.cov((xy - xy.mean(axis=0)).T))
    if eigenvalues[0] <= 0 or eigenvalues[1] / eigenvalues[0] < elongation:
        return None
    axis = eigenvectors[:, 1]
    return math.atan2(float(axis[1]), float(axis[0]))


# The open gripper seen from above (robot.py, skills.OPEN_FOR_GRASP): the fingers' inner faces 10 mm either side of the
# grasp centre along the closing axis, each finger 7 mm thick and 16 mm wide.
FINGER_INNER_M, FINGER_OUTER_M, FINGER_HALF_WIDTH_M = 0.010, 0.017, 0.008


def finger_obstruction(frame: Frame, u: int, v: int, point_xy, finger_yaw: float, table_z: float, *,
                       radius_px: int = 32, raised_m: float = 0.002, margin_m: float = 0.002) -> int:
    """How many depth pixels near (u, v) stand more than `raised_m` above the table where the two open fingertips of a
    grasp at `point_xy`, closing along `finger_yaw`, would come down (with `margin_m` around them): what a finger
    would land on."""
    h, w = frame.depth.shape
    rows = slice(max(0, v - radius_px), min(h, v + radius_px + 1))
    cols = slice(max(0, u - radius_px), min(w, u + radius_px + 1))
    points = back_project_all(frame, rows, cols)
    height = points[..., 2] - table_z
    raised = np.isfinite(height) & (height > raised_m)
    d = points[..., :2] - np.asarray(point_xy, dtype=float)[:2]
    c, s = math.cos(finger_yaw), math.sin(finger_yaw)
    along, across = d[..., 0] * c + d[..., 1] * s, -d[..., 0] * s + d[..., 1] * c
    under = ((np.abs(along) >= FINGER_INNER_M - margin_m) & (np.abs(along) <= FINGER_OUTER_M + margin_m)
             & (np.abs(across) <= FINGER_HALF_WIDTH_M + margin_m))
    return int((raised & under).sum())


def with_ticks(rgb: np.ndarray) -> np.ndarray:
    """The image with pixel ticks on its borders (every TICK_PX, labelled every LABEL_PX)."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=10)
    w, h = image.size
    ink, shadow = (255, 214, 0), (0, 0, 0)
    for u in range(0, w, TICK_PX):
        size = 7 if u % LABEL_PX == 0 else 4
        draw.line([(u, 0), (u, size)], fill=ink)
        draw.line([(u, h - 1 - size), (u, h - 1)], fill=ink)
        if u % LABEL_PX == 0 and u:
            for y in (8, h - 19):
                draw.text((u + 2, y), str(u), fill=ink, font=font, stroke_width=1, stroke_fill=shadow)
    for v in range(0, h, TICK_PX):
        size = 7 if v % LABEL_PX == 0 else 4
        draw.line([(0, v), (size, v)], fill=ink)
        draw.line([(w - 1 - size, v), (w - 1, v)], fill=ink)
        if v % LABEL_PX == 0 and v:
            draw.text((9, v - 5), str(v), fill=ink, font=font, stroke_width=1, stroke_fill=shadow)
            draw.text((w - 9 - 6 * len(str(v)) - 4, v - 5), str(v), fill=ink, font=font, stroke_width=1,
                      stroke_fill=shadow)
    return np.asarray(image)


def encode_jpeg(rgb: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8)).save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


class HeadCamera:
    """Renders the head camera's colour and depth (same pose, same instant) and cuts the work-area window."""

    def __init__(self, world, camera: str = "head_camera", size: tuple[int, int] = RENDER_SIZE,
                 window: tuple[int, int, int, int] = WINDOW):
        self.world, self.camera, self.size, self.window = world, camera, size, window
        self.camera_id = world.model.camera(camera).id
        self.intrinsics = Intrinsics.from_fovy(float(world.model.cam_fovy[self.camera_id]), size, window)
        self._renderer = None

    def capture(self, t: float = 0.0) -> Frame:
        import mujoco

        if self._renderer is None:
            width, height = self.size
            self._renderer = mujoco.Renderer(self.world.model, height, width)
        renderer, data = self._renderer, self.world.data
        renderer.disable_depth_rendering()
        renderer.update_scene(data, camera=self.camera)
        rgb = renderer.render()
        renderer.enable_depth_rendering()
        renderer.update_scene(data, camera=self.camera)
        depth = renderer.render()
        renderer.disable_depth_rendering()
        u0, v0, w, h = self.window
        return Frame(rgb=np.ascontiguousarray(rgb[v0:v0 + h, u0:u0 + w]),
                     depth=np.ascontiguousarray(depth[v0:v0 + h, u0:u0 + w], dtype=np.float32),
                     intrinsics=self.intrinsics, position=data.cam_xpos[self.camera_id].copy(),
                     rotation=data.cam_xmat[self.camera_id].reshape(3, 3).copy(), t=t)

    def close(self) -> None:
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
