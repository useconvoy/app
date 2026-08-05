import { copy, notificationClassLabels } from "@/lexicon";
import { friendlyDateTime } from "@/lib/format";

export interface BellPanelItem {
  id: string;
  /** Notification class; the label always renders through the lexicon. */
  notificationClass: string;
  /** Plain copy, e.g. the checkpoint prompt or the run's name. */
  title: string;
  /**
   * Deep link to the typed action (resume / approve plan / respond), never
   * a generic resolve surface (C4). The caller builds the URL.
   */
  actionUrl: string;
  at?: string | Date;
}

export interface BellPanelListProps {
  items: BellPanelItem[];
  /** Footer link target: the Checkpoints page. */
  checkpointsUrl: string;
}

export function BellPanelList({ items, checkpointsUrl }: BellPanelListProps) {
  return (
    <div>
      {items.length === 0 ? (
        <p className="p-4 text-sm text-muted">{copy.allQuiet}</p>
      ) : (
        <ul className="m-0 list-none divide-y divide-line-soft p-0">
          {items.map((item) => (
            <li key={item.id}>
              <a href={item.actionUrl} className="block px-4 py-3 hover:bg-field">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-[11px] uppercase tracking-wide text-muted">
                    {notificationClassLabels[item.notificationClass] ?? item.notificationClass}
                  </span>
                  {item.at && (
                    <span className="font-mono text-[11px] text-muted">
                      {friendlyDateTime(item.at)}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 block text-sm text-ink">{item.title}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
      <a
        href={checkpointsUrl}
        className="block border-t border-line-soft px-4 py-3 text-sm font-medium text-pine"
      >
        {copy.openCheckpoints}
      </a>
    </div>
  );
}
