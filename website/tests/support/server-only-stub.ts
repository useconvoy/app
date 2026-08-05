/**
 * Vitest alias target for the `server-only` package: the real module throws
 * when bundled into client components, which is exactly right in Next but
 * meaningless under the test runner.
 */
export {};
