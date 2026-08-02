/**
 * Sandbox — public surface. Everything the runner needs to build and drive a
 * simulated environment for one scenario run.
 */

export { createSimClock } from './clock.ts';
export {
  createWorldStore,
  materializeDeep,
  materializeString,
  sha256Hex,
  stableStringify,
} from './world.ts';
export type { SimWorldStore } from './world.ts';
export { createToolGateway } from './gateway.ts';
export type { ToolGatewayOptions } from './gateway.ts';
export { emailEmulator } from './emulators/email.ts';
export { amsEmulator } from './emulators/ams.ts';
export { carrierPortalEmulator } from './emulators/carrierPortal.ts';
export { calendarEmulator } from './emulators/calendar.ts';
export { AGENT_ADDRESS, threadIdForSubject } from './emulators/util.ts';
export { createCounterpartyEngine, expandProfile, mulberry32 } from './counterparty.ts';
export type { CounterpartyEngine, CounterpartyEngineOptions } from './counterparty.ts';
export { createGateScriptEngine } from './gates.ts';
export type { GateResolverFn, GateScriptEngine, GateScriptEngineOptions } from './gates.ts';
export { createSandboxService, defaultEmulators } from './instance.ts';
export type * from './api.ts';
