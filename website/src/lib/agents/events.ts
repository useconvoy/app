/**
 * Event vocabulary for the "Starts when" form. Suggestions per provider —
 * the dotted names the gateway's hook door extracts from deliveries; custom
 * systems type their own eventType.
 */
export const EVENT_SUGGESTIONS: Record<string, string[]> = {
  github: ["issues.opened", "issue_comment.created", "pull_request.opened"],
  slack: ["app_mention", "message"],
};

export function suggestedEvents(provider: string): string[] {
  return EVENT_SUGGESTIONS[provider] ?? [];
}
