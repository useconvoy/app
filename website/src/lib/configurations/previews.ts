/**
 * Robot previews the website ships: a turntable loop of a simulated robot in its idle pose, and its
 * first frame as a still. The files live under `public/sim/<id>/` and are rendered by
 * `integrations/simulation/scripts/render_robot_preview.py`. A document names a preview only by id
 * (`RobotSpec.preview`, one of `ROBOT_PREVIEW_IDS`), so it can never point the page at another file.
 */
import type { RobotPreviewId, RobotSpec } from "./types";

export interface RobotPreview {
  id: RobotPreviewId;
  /** What the picture shows: the text alternative of the loop and the still. */
  label: string;
  /** The first frame: shown before playback and under reduced motion. */
  poster: string;
  /** The loop, preferred format first. */
  sources: ReadonlyArray<{ src: string; type: string }>;
  /** Pixel size of the poster and the loop. */
  width: number;
  height: number;
}

export const ROBOT_PREVIEWS: Readonly<Record<RobotPreviewId, RobotPreview>> = {
  "bimanual-station": {
    id: "bimanual-station",
    label: "Simulated two-arm robot on a wheeled base",
    poster: "/sim/bimanual-station/poster.webp",
    sources: [
      { src: "/sim/bimanual-station/turntable.webm", type: "video/webm; codecs=\"vp9\"" },
      { src: "/sim/bimanual-station/turntable.mp4", type: "video/mp4; codecs=\"avc1.64001F\"" },
    ],
    width: 960,
    height: 640,
  },
};

/** The preview a robot names, or null when it names none or an id this website does not ship. */
export function robotPreview(robot: Pick<RobotSpec, "preview">): RobotPreview | null {
  const id = robot.preview;
  return typeof id === "string" && Object.hasOwn(ROBOT_PREVIEWS, id) ? ROBOT_PREVIEWS[id as RobotPreviewId] : null;
}
