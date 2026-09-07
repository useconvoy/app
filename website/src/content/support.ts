/**
 * The supported-configuration matrix. It stays empty until a tested
 * configuration exists; a model or hardware name never lands here because a
 * logo or a vendor page mentions it. Each entry must name the full tuple and
 * the evidence behind it.
 */
export interface SupportedConfiguration {
  model: string;
  processing: string;
  runtime: string;
  hardware: string;
  controller: string;
  task: string;
  evidence: string;
  testedOn: string;
}

export const SUPPORTED_CONFIGURATIONS: readonly SupportedConfiguration[] = [];

export const SUPPORT_STATUS =
  "No supported configurations are published yet. Scope is defined with each design partner." as const;
