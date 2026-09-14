/**
 * Tested configurations, bounded by their linked physical evidence. A listed
 * text-inference configuration does not establish general robot compatibility.
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

export const SUPPORTED_CONFIGURATIONS: readonly SupportedConfiguration[] = [
  {
    model: "Qwen2.5-1.5B-Instruct Q4_K_M GGUF",
    processing: "Text-only conversation; 2,048-token context; up to 128 output tokens",
    runtime: "llama.cpp with CUDA offload on 29/29 layers",
    hardware: "NVIDIA Jetson Orin Nano Developer Kit Super, 8 GB",
    controller: "No robot controller or motion-control interface in this demo",
    task: "Two-turn text Chat with matching device trace and usage evidence",
    evidence: "https://github.com/useconvoy/app/blob/main/control-plane/docs/VERIFICATION.md#browser-chat-on-the-physical-nano--source-4df467b",
    testedOn: "2026-09-14",
  },
];

export const SUPPORT_STATUS =
  "One text-inference configuration has physical verification: Qwen2.5-1.5B-Instruct Q4_K_M on the project's Jetson Orin Nano. Broader model and robot support is defined per deployment." as const;
