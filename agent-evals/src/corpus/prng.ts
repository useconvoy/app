/**
 * Seeded PRNG for corpus generation — mulberry32. Every stochastic choice in
 * the generator flows through ONE Prng instance in a fixed call order, which
 * is what makes `same seed → byte-identical corpus` hold.
 */

export type Rand = () => number;

/** mulberry32: fast 32-bit seeded generator, uniform in [0, 1). */
export function mulberry32(seed: number): Rand {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface Prng {
  /** Uniform float in [0, 1). */
  next(): number;
  /** Uniform integer in [min, max] (both inclusive). */
  int(min: number, max: number): number;
  /** Uniform float in [min, max). */
  uniform(min: number, max: number): number;
  /** Pick one element. Throws on empty input. */
  pick<T>(items: readonly T[]): T;
  /** Fisher–Yates shuffle into a NEW array (input untouched). */
  shuffle<T>(items: readonly T[]): T[];
}

export function createPrng(seed: number): Prng {
  const next = mulberry32(seed);
  const int = (min: number, max: number): number => {
    if (max < min) throw new Error(`prng.int: max ${max} < min ${min}`);
    return min + Math.floor(next() * (max - min + 1));
  };
  return {
    next,
    int,
    uniform: (min, max) => min + next() * (max - min),
    pick: (items) => {
      if (items.length === 0) throw new Error('prng.pick: empty array');
      const v = items[int(0, items.length - 1)];
      return v as (typeof items)[number];
    },
    shuffle: (items) => {
      const out = [...items];
      for (let i = out.length - 1; i > 0; i--) {
        const j = int(0, i);
        const a = out[i] as (typeof items)[number];
        out[i] = out[j] as (typeof items)[number];
        out[j] = a;
      }
      return out;
    },
  };
}
