/** The hosted profiles' four action values: normalized end-effector deltas and the gripper. */
export const HOSTED_AXES = ["X", "Y", "Z", "Grip"] as const;

/**
 * One label per action value of an episode replay: the recording's own labels when
 * there is one per value (offline episodes may name theirs), else X/Y/Z/Grip for
 * four values, else a1…aN.
 */
export function actionLabels(labels: readonly unknown[] | null | undefined, width: number): string[] {
  if (labels?.length === width && labels.every(label => typeof label === "string" && label.trim())) return labels as string[];
  return width === HOSTED_AXES.length ? [...HOSTED_AXES] : Array.from({ length: width }, (_, i) => `a${i + 1}`);
}

/** The data URL type of a replay frame: PNG unless the frame says JPEG (offline uploads). */
export const frameMediaType = (type: unknown) => type === "image/jpeg" ? "image/jpeg" : "image/png";
