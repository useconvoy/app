/**
 * Judge calibration — a table, not a doc. Every judge verdict on the
 * calibration slice is stored against a human label; a judge version may gate
 * promotion only once it clears the ex-ante thresholds. Until then its
 * verdicts are advisory: reported, never gating.
 *
 * Thresholds (fixed, versioned with the harness):
 *   >= 30 labels, false-accept <= 5%, false-reject <= 15%.
 * FA is stricter because a false accept in a trust product is the killer.
 *   false-accept = judge 'pass' on a human-'fail' case / human-'fail' cases
 *   false-reject = judge 'fail' on a human-'pass' case / human-'pass' cases
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

export const CALIBRATION_MIN_LABELS = 30;
export const FALSE_ACCEPT_MAX = 0.05;
export const FALSE_REJECT_MAX = 0.15;

export interface CalibrationLabel {
  /** Pointer back to the graded artifact/run the label came from. */
  ref: string;
  judge: 'pass' | 'fail';
  human: 'pass' | 'fail';
}

export interface CalibrationRecord {
  judgeId: string;
  promptHash: string;
  labels: CalibrationLabel[];
}

export interface CalibrationStore {
  records: CalibrationRecord[];
}

export function emptyCalibrationStore(): CalibrationStore {
  return { records: [] };
}

/** Missing path / missing file → empty store (all judges advisory). */
export function loadCalibrationStore(path?: string): CalibrationStore {
  if (!path || !existsSync(path)) return emptyCalibrationStore();
  const raw: unknown = JSON.parse(readFileSync(path, 'utf8'));
  if (Array.isArray(raw)) return { records: raw as CalibrationRecord[] };
  if (raw !== null && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>;
    if (Array.isArray(obj.records)) return { records: obj.records as CalibrationRecord[] };
    if (typeof obj.judgeId === 'string') return { records: [raw as CalibrationRecord] };
  }
  return emptyCalibrationStore();
}

export function saveCalibrationStore(path: string, store: CalibrationStore): void {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(store, null, 2));
}

export interface CalibrationStats {
  labelCount: number;
  falseAcceptRate: number;
  falseRejectRate: number;
}

export function calibrationStats(labels: CalibrationLabel[]): CalibrationStats {
  const humanFail = labels.filter((l) => l.human === 'fail');
  const humanPass = labels.filter((l) => l.human === 'pass');
  const fa = humanFail.length === 0 ? 0 : humanFail.filter((l) => l.judge === 'pass').length / humanFail.length;
  const fr = humanPass.length === 0 ? 0 : humanPass.filter((l) => l.judge === 'fail').length / humanPass.length;
  return { labelCount: labels.length, falseAcceptRate: fa, falseRejectRate: fr };
}

/** Calibrated ⇔ >=30 labels AND FA <= 5% AND FR <= 15% for (judgeId, promptHash). */
export function judgeIsCalibrated(
  store: CalibrationStore,
  judgeId: string,
  promptHash: string
): boolean {
  const rec = store.records.find((r) => r.judgeId === judgeId && r.promptHash === promptHash);
  if (!rec) return false;
  const stats = calibrationStats(rec.labels);
  return (
    stats.labelCount >= CALIBRATION_MIN_LABELS &&
    stats.falseAcceptRate <= FALSE_ACCEPT_MAX &&
    stats.falseRejectRate <= FALSE_REJECT_MAX
  );
}
