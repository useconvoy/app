import coreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "src/lib/api/schema.d.ts",
    ],
  },
  ...coreWebVitals,
  ...nextTypescript,
  {
    settings: {
      react: { version: "19" },
    },
    rules: {
      // The generated client is the only door for domain types; runtime
      // internals must never be imported into the website.
      "no-restricted-imports": [
        "error",
        { patterns: [{ group: ["**/convoy_runtime/**", "**/agent-runtime/**"] }] },
      ],
    },
  },
];

export default config;
