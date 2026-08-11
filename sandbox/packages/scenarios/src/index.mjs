function matches(candidate, expected) {
  return Object.entries(expected).every(([key, value]) => {
    if (key.endsWith("Includes")) {
      const actualKey = key.slice(0, -"Includes".length);
      return String(candidate[actualKey] ?? "").toLowerCase().includes(String(value).toLowerCase());
    }
    return candidate[key] === value;
  });
}

function collectionAt(state, provider, collection) {
  let value = state.providers[provider];
  for (const part of collection.split(".")) value = value?.[part];
  return value;
}

function evaluateCriterion(state, criterion) {
  if (criterion.type === "collection_contains") {
    const collection = collectionAt(state, criterion.provider, criterion.collection) ?? [];
    const count = collection.filter((candidate) => matches(candidate, criterion.match)).length;
    return { passed: count >= (criterion.minimum ?? 1), evidence: `${count} matching objects` };
  }
  if (criterion.type === "sheet_contains_row") {
    const spreadsheets = state.providers.google?.spreadsheets ?? [];
    const spreadsheet = spreadsheets.find((candidate) => candidate.spreadsheetId === criterion.spreadsheetId);
    const rows = spreadsheet?.sheets?.[criterion.sheet] ?? [];
    const count = rows.filter((row) => criterion.values.every((value, index) => row[index] === value)).length;
    return { passed: count >= (criterion.minimum ?? 1), evidence: `${count} matching rows` };
  }
  if (criterion.type === "github_issue") {
    const repo = state.providers.github?.repositories?.find(
      (candidate) => candidate.fullName === criterion.repository,
    );
    const count = (repo?.issues ?? []).filter((issue) => matches(issue, criterion.match)).length;
    return { passed: count >= (criterion.minimum ?? 1), evidence: `${count} matching issues` };
  }
  if (criterion.type === "audit_event") {
    const count = state.auditLog.filter((entry) => entry.event === criterion.event).length;
    return { passed: count >= (criterion.minimum ?? 1), evidence: `${count} matching audit events` };
  }
  throw new Error(`Unknown scenario criterion: ${criterion.type}`);
}

export function evaluateScenario(state, scenario) {
  const criteria = scenario.criteria.map((criterion) => ({
    id: criterion.id,
    label: criterion.label,
    weight: criterion.weight,
    ...evaluateCriterion(state, criterion),
  }));
  const earned = criteria.filter((criterion) => criterion.passed).reduce((sum, criterion) => sum + criterion.weight, 0);
  const possible = criteria.reduce((sum, criterion) => sum + criterion.weight, 0);
  return {
    scenarioId: scenario.id,
    passed: criteria.every((criterion) => criterion.passed),
    score: possible === 0 ? 1 : earned / possible,
    earned,
    possible,
    criteria,
  };
}
