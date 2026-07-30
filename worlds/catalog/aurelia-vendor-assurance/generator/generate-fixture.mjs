import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const modulePath = fileURLToPath(import.meta.url);
const worldDirectory = path.resolve(path.dirname(modulePath), "..");
const domains = ["security", "privacy", "architecture", "legal"];
const categories = [
  "AI and data",
  "Cloud infrastructure",
  "Customer support",
  "Finance",
  "Identity and security",
  "Product analytics",
  "Sales and marketing",
  "Workforce operations",
];
const owners = [
  "Priya Shah",
  "Elliot Park",
  "Maya Chen",
  "Jordan Lee",
  "Ana Santos",
];
const prefixes = [
  "Atlas",
  "Beacon",
  "Cedar",
  "Delta",
  "Evergreen",
  "Foundry",
  "Granite",
  "Harbor",
  "Ion",
  "Juniper",
];
const suffixes = [
  "AI",
  "Cloud",
  "Data",
  "Labs",
  "Networks",
  "Security",
];

function hashSeed(value) {
  let hash = 2166136261;
  for (const character of value) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function randomFor(seed) {
  let state = hashSeed(seed) || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return (state >>> 0) / 4294967296;
  };
}

function shuffledIndexes(length, random) {
  const indexes = Array.from({ length }, (_, index) => index);
  for (let index = indexes.length - 1; index > 0; index -= 1) {
    const swap = Math.floor(random() * (index + 1));
    [indexes[index], indexes[swap]] = [indexes[swap], indexes[index]];
  }
  return indexes;
}

function vendorName(index) {
  const prefix = prefixes[index % prefixes.length];
  const suffix = suffixes[Math.floor(index / prefixes.length) % suffixes.length];
  return `${prefix} ${suffix}`;
}

function requirementTitle(domain) {
  return {
    security: "Current independent security controls report",
    privacy: "Signed data-processing and retention evidence",
    architecture: "Current production architecture and isolation evidence",
    legal: "Executed commercial and regulatory terms",
  }[domain];
}

function validSummary(domain) {
  return {
    security:
      "Independent controls report is current; administrative MFA and audit logging are in scope.",
    privacy:
      "Executed DPA limits retention to 30 days and lists all subprocessors.",
    architecture:
      "Production diagram documents tenant isolation, encryption, backups, and regional boundaries.",
    legal:
      "Executed agreement includes security, breach-notification, audit, and deletion obligations.",
  }[domain];
}

function contradictorySummary(domain) {
  return {
    security:
      "The controls report states that MFA remains optional for two production administrator roles.",
    privacy:
      "The retention schedule permits indefinite storage of derived customer transcript data.",
    architecture:
      "The architecture diagram shows shared unencrypted backup storage across tenant environments.",
    legal:
      "The uploaded DPA is an unsigned draft and excludes the production analytics subprocessors.",
  }[domain];
}

function parseArguments(args) {
  const values = {
    seed: "aurelia-fy26-q3",
    vendors: 50,
    exceptions: 20,
    output: worldDirectory,
  };
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    const value = args[index + 1];
    if (flag === "--seed") values.seed = value;
    else if (flag === "--vendors") values.vendors = Number(value);
    else if (flag === "--exceptions") values.exceptions = Number(value);
    else if (flag === "--output") values.output = path.resolve(value);
    else throw new Error(`Unknown generator argument: ${flag}`);
  }
  if (!Number.isInteger(values.vendors) || values.vendors < 1 || values.vendors > 250) {
    throw new Error("--vendors must be an integer between 1 and 250.");
  }
  const requirementCount = values.vendors * domains.length;
  if (
    !Number.isInteger(values.exceptions) ||
    values.exceptions < 0 ||
    values.exceptions > requirementCount
  ) {
    throw new Error(
      `--exceptions must be an integer between 0 and ${requirementCount}.`,
    );
  }
  return values;
}

export function buildPortfolio({
  seed = "aurelia-fy26-q3",
  vendorCount = 50,
  exceptionCount = 20,
} = {}) {
  const random = randomFor(seed);
  const vendors = [];
  const requirements = [];

  for (let index = 0; index < vendorCount; index += 1) {
    const number = String(index + 1).padStart(3, "0");
    const criticality =
      index % 5 === 0 ? "critical" : index % 2 === 0 ? "high" : "standard";
    const vendor = {
      id: `vendor-${number}`,
      name: vendorName(index),
      category: categories[index % categories.length],
      annual_cost_usd: 30000 + ((index * 37991) % 470000),
      criticality,
      data_access:
        index % 3 === 0
          ? "Customer content and production metadata"
          : index % 3 === 1
            ? "Employee identity and operational metadata"
            : "Aggregated product and billing metadata",
      business_owner: owners[index % owners.length],
      review_due: `2026-08-${String(1 + (index % 27)).padStart(2, "0")}`,
      review_status: "due",
    };
    vendors.push(vendor);
    for (const domain of domains) {
      requirements.push({
        id: `req-${vendor.id}-${domain}`,
        vendor_id: vendor.id,
        domain,
        title: requirementTitle(domain),
        max_age_days:
          domain === "security" || domain === "architecture" ? 180 : 365,
        required: true,
      });
    }
  }

  const problemIndexes = new Set(
    shuffledIndexes(requirements.length, random).slice(0, exceptionCount),
  );
  const evidence = [];
  const expectedFindings = [];
  let exceptionSequence = 0;

  for (const [index, requirement] of requirements.entries()) {
    const vendor = vendors.find((item) => item.id === requirement.vendor_id);
    let outcome = "valid";
    if (problemIndexes.has(index)) {
      outcome = ["missing", "stale", "contradictory"][
        exceptionSequence % 3
      ];
      exceptionSequence += 1;
    }

    const evidenceIds = [];
    if (outcome !== "missing") {
      const evidenceId = `evidence-${requirement.vendor_id}-${requirement.domain}`;
      evidence.push({
        id: evidenceId,
        vendor_id: requirement.vendor_id,
        requirement_id: requirement.id,
        domain: requirement.domain,
        title: `${vendor.name} ${requirement.domain} evidence`,
        source_system: "vendor-assurance-drive",
        collected_at: outcome === "stale" ? "2024-01-15" : "2026-07-15",
        digest: `sha256:${hashSeed(`${seed}:${evidenceId}`).toString(16).padStart(8, "0")}`,
        summary:
          outcome === "contradictory"
            ? contradictorySummary(requirement.domain)
            : validSummary(requirement.domain),
      });
      evidenceIds.push(evidenceId);
    }

    const severity =
      outcome === "contradictory"
        ? "critical"
        : outcome === "missing"
          ? "high"
          : outcome === "stale"
            ? "medium"
            : null;
    expectedFindings.push({
      vendor_id: requirement.vendor_id,
      requirement_id: requirement.id,
      domain: requirement.domain,
      outcome,
      severity,
      evidence_ids: evidenceIds,
      requires_approval:
        outcome !== "valid" &&
        (severity === "critical" || vendor.criticality === "critical"),
    });
  }

  const state = {
    company: {
      name: "Aurelia Cloud",
      quarter: "FY26 Q3",
      current_date: "2026-07-30",
    },
    people: [
      {
        id: "person-priya",
        name: "Priya Shah",
        role: "Chief Information Security Officer",
      },
      {
        id: "person-elliot",
        name: "Elliot Park",
        role: "VP Legal",
      },
      {
        id: "person-maya",
        name: "Maya Chen",
        role: "VP Revenue Operations",
      },
      {
        id: "person-vinayaka",
        name: "Vinayaka",
        role: "Executive sponsor",
      },
    ],
    vendors,
    evidence_requirements: requirements,
    evidence,
    findings: [],
    exceptions: [],
    tasks: [],
    documents: [],
    approvals: [],
    channels: [
      {
        id: "ch-vendor-assurance",
        name: "vendor-assurance",
        kind: "internal",
      },
    ],
    messages: [
      {
        id: "msg_0001",
        channel_id: "ch-vendor-assurance",
        author: "priya",
        text: `Complete the FY26 Q3 assurance review for all ${vendorCount} production vendors.`,
        created_at: "2026-07-30T08:30:00-07:00",
      },
    ],
    audit_log: [],
    runtime: {
      reset_at: null,
      action_count: 0,
      denied_action_count: 0,
      fabricated_evidence_count: 0,
      idempotent_replay_count: 0,
    },
  };

  const groundTruth = {
    seed,
    summary: {
      vendor_count: vendors.length,
      requirement_count: requirements.length,
      exception_count: expectedFindings.filter(
        (item) => item.outcome !== "valid",
      ).length,
      approval_count: expectedFindings.filter(
        (item) => item.requires_approval,
      ).length,
    },
    expected_findings: expectedFindings,
  };

  const preexistingFindings = expectedFindings
    .filter((item) => item.outcome !== "valid")
    .slice(0, 3);
  const preexistingExceptions = preexistingFindings.map((expected, index) => ({
    id: `exception_${String(index + 1).padStart(4, "0")}`,
    status: "open",
    created_by: "prior-quarter-review",
    created_at: "2026-07-22T10:00:00.000Z",
    vendor_id: expected.vendor_id,
    requirement_id: expected.requirement_id,
    severity: expected.severity,
    reason: `${expected.domain} evidence is ${expected.outcome}.`,
    idempotency_key: `exception:${expected.requirement_id}`,
  }));
  const preexistingTasks = preexistingFindings.map((expected, index) => ({
    id: `task_${String(index + 1).padStart(4, "0")}`,
    status: "open",
    created_by: "prior-quarter-review",
    created_at: "2026-07-22T10:05:00.000Z",
    vendor_id: expected.vendor_id,
    requirement_id: expected.requirement_id,
    exception_id: `exception_${String(index + 1).padStart(4, "0")}`,
    title: `Remediate ${expected.domain} evidence for ${expected.vendor_id}`,
    owner: owners[(index + 1) % owners.length],
    due_date: `2026-08-${String(index + 2).padStart(2, "0")}`,
    idempotency_key: `task:${expected.requirement_id}`,
  }));
  const preexistingApproved = preexistingFindings.find(
    (item) => item.requires_approval,
  );
  const preexistingApprovals = preexistingApproved
    ? [
        {
          id: "approval_0001",
          requested_by: "prior-quarter-review",
          requested_at: "2026-07-22T10:10:00.000Z",
          status: "approved",
          resolved_by: "person-priya",
          resolved_at: "2026-07-22T12:00:00.000Z",
          type: "vendor_exception",
          target_id: preexistingApproved.requirement_id,
          reason: `${preexistingApproved.severity} ${preexistingApproved.domain} exception requires human risk acceptance.`,
          idempotency_key: `approval:${preexistingApproved.requirement_id}`,
        },
      ]
    : [];
  state.exceptions = preexistingExceptions;
  state.tasks = preexistingTasks;
  state.approvals = preexistingApprovals;

  const actions = [];
  let remediationIndex = 0;
  for (const [index, expected] of expectedFindings.entries()) {
    const actor = `vendor-investigator-${String((index % 16) + 1).padStart(2, "0")}`;
    actions.push({
      actor,
      tool: "evidence.record_finding",
      input: {
        vendor_id: expected.vendor_id,
        requirement_id: expected.requirement_id,
        outcome: expected.outcome,
        rationale:
          expected.outcome === "valid"
            ? "The source artifact is current, scoped to production, and satisfies the requirement."
            : `The source record establishes a ${expected.outcome} evidence exception.`,
        evidence_ids: expected.evidence_ids,
        idempotency_key: `finding:${expected.requirement_id}`,
      },
    });
    if (expected.outcome === "valid") continue;

    remediationIndex += 1;
    const exceptionId = `exception_${String(remediationIndex).padStart(4, "0")}`;
    actions.push({
      actor: "exception-coordinator",
      tool: "exception.create",
      input: {
        vendor_id: expected.vendor_id,
        requirement_id: expected.requirement_id,
        severity: expected.severity,
        reason: `${expected.domain} evidence is ${expected.outcome}.`,
        idempotency_key: `exception:${expected.requirement_id}`,
      },
    });
    actions.push({
      actor: "remediation-coordinator",
      tool: "work.create_task",
      input: {
        vendor_id: expected.vendor_id,
        requirement_id: expected.requirement_id,
        exception_id: exceptionId,
        title: `Remediate ${expected.domain} evidence for ${expected.vendor_id}`,
        owner: owners[remediationIndex % owners.length],
        due_date: `2026-08-${String(1 + (remediationIndex % 27)).padStart(2, "0")}`,
        idempotency_key: `task:${expected.requirement_id}`,
      },
    });
    if (expected.requires_approval) {
      actions.push({
        actor: "risk-gate-coordinator",
        tool: "approval.request",
        input: {
          type: "vendor_exception",
          target_id: expected.requirement_id,
          reason: `${expected.severity} ${expected.domain} exception requires human risk acceptance.`,
          idempotency_key: `approval:${expected.requirement_id}`,
        },
      });
    }
  }

  actions.push({
    actor: "portfolio-synthesizer",
    tool: "drive.write_document",
    input: {
      title: "FY26 Q3 production vendor assurance report",
      document_type: "portfolio_assurance_report",
      body: `${vendors.length} vendors and ${requirements.length} evidence requirements reviewed; ${exceptionCount} material exceptions routed into remediation.`,
      summary_metrics: {
        vendors_reviewed: vendors.length,
        requirements_reviewed: requirements.length,
        exceptions_found: exceptionCount,
      },
      idempotency_key: `report:${seed}`,
    },
  });

  return { state, groundTruth, actions };
}

export async function writeGeneratedPortfolio(options = {}) {
  const output = path.resolve(options.output ?? worldDirectory);
  const generated = buildPortfolio({
    seed: options.seed,
    vendorCount: options.vendors,
    exceptionCount: options.exceptions,
  });
  await mkdir(output, { recursive: true });
  await Promise.all([
    writeFile(
      path.join(output, "initial-state.json"),
      `${JSON.stringify(generated.state, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(output, "ground-truth.json"),
      `${JSON.stringify(generated.groundTruth, null, 2)}\n`,
      "utf8",
    ),
    writeFile(
      path.join(output, "successful-run.json"),
      `${JSON.stringify(generated.actions, null, 2)}\n`,
      "utf8",
    ),
  ]);
  return generated;
}

if (process.argv[1] && path.resolve(process.argv[1]) === modulePath) {
  const options = parseArguments(process.argv.slice(2));
  const generated = await writeGeneratedPortfolio(options);
  console.log(
    `Generated ${generated.state.vendors.length} vendors, ${generated.state.evidence_requirements.length} requirements, and ${generated.groundTruth.summary.exception_count} exceptions in ${options.output}.`,
  );
}
