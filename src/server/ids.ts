let counter = 0;

export function id(prefix: string): string {
  counter = (counter + 1) % 46656;
  return `${prefix}_${Date.now().toString(36)}${counter.toString(36).padStart(3, "0")}${Math.random()
    .toString(36)
    .slice(2, 6)}`;
}

export function now(): string {
  return new Date().toISOString();
}
