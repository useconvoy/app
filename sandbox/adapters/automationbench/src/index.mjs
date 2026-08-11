const adapters = {
  "slack.send_channel_message": ({ channel, channel_id, message, text }) => ({
    provider: "slack",
    method: "POST",
    path: "/api/chat.postMessage",
    body: { channel: channel_id ?? channel, text: text ?? message },
  }),
  "slack.send_message": ({ channel, channel_id, message, text }) => ({
    provider: "slack",
    method: "POST",
    path: "/api/chat.postMessage",
    body: { channel: channel_id ?? channel, text: text ?? message },
  }),
  "google_sheets.add_row": ({ spreadsheet_id, spreadsheetId, range = "Sheet1!A1", values }) => ({
    provider: "google",
    method: "POST",
    path: `/sheets/v4/spreadsheets/${spreadsheetId ?? spreadsheet_id}/values/${encodeURIComponent(range)}:append`,
    query: { valueInputOption: "USER_ENTERED", insertDataOption: "INSERT_ROWS" },
    body: { values: Array.isArray(values?.[0]) ? values : [values] },
  }),
  "github.create_issue": ({ repo, title, body = "" }) => ({
    provider: "github",
    method: "POST",
    path: `/repos/${repo}/issues`,
    body: { title, body },
  }),
};

export function translateAutomationBenchToolCall(call) {
  const key = `${call.app}.${call.action}`;
  const adapter = adapters[key];
  if (!adapter) throw new Error(`Unsupported AutomationBench tool call: ${key}`);
  return { ...adapter(call.args ?? {}), headers: {}, query: {} };
}

export function supportedAutomationBenchCalls() {
  return Object.keys(adapters);
}
