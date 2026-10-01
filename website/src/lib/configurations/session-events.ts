/**
 * A 401 from any Configurations request (workspace document, live device
 * polling) ends the browser session the same way: the session gate listens
 * here and returns to sign-in. Decoupled so live polling, which sits above the
 * gate in the /app layout, can report it.
 */
type Listener = () => void;
const listeners = new Set<Listener>();

export function onSessionExpired(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function notifySessionExpired(): void {
  for (const listener of [...listeners]) listener();
}
