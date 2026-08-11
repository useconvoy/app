import { runHttpContracts } from "../packages/contract-testkit/src/index.mjs";

const suites = [];

if (process.env.OFFICIAL_SLACK_BOT_TOKEN) {
  suites.push(runHttpContracts({
    baseUrl: "https://slack.com/api",
    variables: { token: process.env.OFFICIAL_SLACK_BOT_TOKEN },
    contracts: [{
      name: "Official Slack conversations.list",
      request: {
        method: "GET",
        path: "/conversations.list?limit=1&types=public_channel",
        headers: { authorization: "Bearer {{token}}" },
      },
      expect: { status: 200, requiredFields: ["ok", "channels"], equals: { ok: true } },
    }],
  }));
}

if (process.env.OFFICIAL_GITHUB_TOKEN && process.env.OFFICIAL_GITHUB_REPOSITORY) {
  suites.push(runHttpContracts({
    baseUrl: "https://api.github.com",
    variables: {
      token: process.env.OFFICIAL_GITHUB_TOKEN,
      repository: process.env.OFFICIAL_GITHUB_REPOSITORY,
    },
    contracts: [{
      name: "Official GitHub issues list",
      request: {
        method: "GET",
        path: "/repos/{{repository}}/issues?state=open&per_page=1",
        headers: {
          authorization: "Bearer {{token}}",
          accept: "application/vnd.github+json",
          "x-github-api-version": "2022-11-28"
        },
      },
      expect: { status: 200 },
    }],
  }));
}

if (process.env.OFFICIAL_GOOGLE_ACCESS_TOKEN && process.env.OFFICIAL_GOOGLE_FOLDER_ID) {
  suites.push(runHttpContracts({
    baseUrl: "https://www.googleapis.com/drive/v3",
    variables: {
      token: process.env.OFFICIAL_GOOGLE_ACCESS_TOKEN,
      folder: encodeURIComponent(`'${process.env.OFFICIAL_GOOGLE_FOLDER_ID}' in parents and trashed = false`),
    },
    contracts: [{
      name: "Official Google Drive file list",
      request: {
        method: "GET",
        path: "/files?q={{folder}}&pageSize=1&fields=files(id,name),nextPageToken",
        headers: { authorization: "Bearer {{token}}" },
      },
      expect: { status: 200, requiredFields: ["files"] },
    }],
  }));
}

if (suites.length === 0) {
  console.log("No official provider credentials configured; skipping nightly contracts.");
} else {
  const results = (await Promise.all(suites)).flat();
  console.log(JSON.stringify(results, null, 2));
}
