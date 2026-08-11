import assert from "node:assert/strict";

function resolvePath(value, dottedPath) {
  return dottedPath.split(".").reduce((current, key) => current?.[key], value);
}

export function assertContractResponse(contract, response, body) {
  assert.equal(response.status, contract.expect.status, `${contract.name}: unexpected HTTP status`);
  for (const field of contract.expect.requiredFields ?? []) {
    assert.notEqual(resolvePath(body, field), undefined, `${contract.name}: missing ${field}`);
  }
  for (const [field, expected] of Object.entries(contract.expect.equals ?? {})) {
    assert.deepEqual(resolvePath(body, field), expected, `${contract.name}: ${field} differs`);
  }
}

export async function runHttpContracts({ baseUrl, contracts, fetchImpl = fetch, variables = {} }) {
  const results = [];
  for (const contract of contracts) {
    const interpolate = (value) => typeof value === "string"
      ? value.replace(/\{\{([^}]+)\}\}/g, (_, key) => variables[key] ?? "")
      : value;
    const response = await fetchImpl(`${baseUrl}${interpolate(contract.request.path)}`, {
      method: contract.request.method,
      headers: Object.fromEntries(Object.entries(contract.request.headers ?? {}).map(([key, value]) => [key, interpolate(value)])),
      body: contract.request.body ? JSON.stringify(contract.request.body) : undefined,
    });
    const body = await response.json();
    assertContractResponse(contract, response, body);
    results.push({ name: contract.name, status: "passed" });
  }
  return results;
}
