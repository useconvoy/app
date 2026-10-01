"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef } from "react";
import { ACTIVITY_LABEL, activitySubject, attentionCounts, cardVerdict, INDEX_QUERY, noResultsTitle, parseFilter, parseSort, robotCountsLabel, SORT_OPTIONS, stackRows, STATUS_FILTERS } from "@/lib/configurations/cards";
import type { CardVerdict } from "@/lib/configurations/cards";
import { attentionRobots, boundRobots, configurationCounts, configurationSummary, listConfigurations, recentActivity, successShare, useWorkspace } from "@/lib/configurations/client";
import type { ConfigFilter, ConfigSort, LiveMap } from "@/lib/configurations/client";
import { fmtCount, fmtDate, fmtRelative, fmtShare, fmtTime, fmtUpdated } from "@/lib/configurations/format";
import { LIVE_POLL_INTERVAL_MS } from "@/lib/configurations/live";
import { routes } from "@/lib/configurations/routes";
import type { Configuration, ConvoyWorkspace } from "@/lib/configurations/types";
import { AppShell, type Crumb } from "../AppShell";
import { Badge, ConfigStatusBadges, NotReported, ProvenanceBadge, RunStatus } from "../Badges";
import { AddConfigurationTile, ConfigCard, ConfigCards } from "../ConfigCard";
import { Panel } from "../Facts";
import { useNow, useQueryState } from "../hooks";
import { Icon } from "../Icons";
import { useLiveRobots } from "../LiveDeviceProvider";
import { ImportWorkspaceButton, WorkspaceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { EmptyState, LoadingState } from "../States";
import { FilterChips, SelectField, Toolbar } from "../Toolbar";

const CRUMBS: Crumb[] = [{ label: "Configurations" }];
/** Newest activity events shown in the feed. */
const FEED_LIMIT = 8;
const plural = (count: number, noun: string) => `${count.toLocaleString("en-US")} ${noun}${count === 1 ? "" : "s"}`;
const VERDICT_ICON = { good: "check", warning: "warning", blocked: "blocked" } as const;

/**
 * Screen 1 · Configurations index (`/app/configurations`, `?q=`, `?status=`, `?sort=`):
 * search, status chips and sort kept in the URL; one card per configuration (stack,
 * revisions, robot counts, robots needing attention by their displayed health
 * (stored health, live readings and flags), the latest gate run with n and provenance, a verdict line); the add
 * tile; recent activity.
 */
export function ConfigurationsIndexPage() {
  const ws = useWorkspace();
  const now = useNow();
  const [queryParam, setQueryParam] = useQueryState(INDEX_QUERY.search);
  const [statusParam, setStatusParam] = useQueryState(INDEX_QUERY.status);
  const [sortParam, setSortParam] = useQueryState(INDEX_QUERY.sort);
  const search = queryParam ?? "";
  const attached = useMemo(() => (ws.workspace?.robots ?? []).filter(robot => robot.configId !== null), [ws.workspace]);
  const live = useLiveRobots(attached);
  if (ws.status === "loading") return <AppShell crumbs={CRUMBS}><LoadingState /></AppShell>;

  const workspace = ws.workspace;
  const status = parseFilter(statusParam), sort = parseSort(sortParam);
  const total = workspace.configurations.length;
  const shown = listConfigurations(workspace, { query: search, status, sort });
  const counts = configurationCounts(workspace, search);
  const filtered = !!search.trim() || status !== "all";
  const bound = boundRobots(workspace).filter(robot => robot.configId !== null).length;
  const onSearch = (value: string) => setQueryParam(value.trim() ? value : null, { replace: true });
  const onStatus = (id: ConfigFilter) => setStatusParam(id === "all" ? null : id, { replace: true });
  const onSort = (value: ConfigSort) => setSortParam(value === "activity" ? null : value, { replace: true });
  const clearFilters = () => { setQueryParam(null, { replace: true }); setStatusParam(null, { replace: true }); };

  return <AppShell crumbs={CRUMBS}>
    <PageHeader eyebrow="Configurations" title="Configurations"
      actions={total > 0 && <Link className="btn btn-primary cfg-btn" href={routes.newConfiguration()}>Add configuration <Icon name="plus" /></Link>}
      meta={`${plural(total, "configuration")} · ${plural(attached.length, "robot")} attached · ${fmtUpdated(workspace.meta.updatedAt, null)}${bound ? ` · Device readings refresh every ${LIVE_POLL_INTERVAL_MS / 1000} seconds` : ""}`} />
    <WorkspaceNotice workspace={workspace} />

    {total === 0
      ? <EmptyState icon="info" title="No configurations in this workspace yet."
        text="A configuration pairs a robot with edge hardware, edge and cloud models, routing and a safety envelope. New configurations start from one the workspace already describes, so import a workspace file that includes at least one."
        action={<ImportWorkspaceButton />} />
      : <>
        <Toolbar>
          <SearchBox value={search} onChange={onSearch} />
          <FilterChips label="Filter by status" value={status} onChange={onStatus} options={STATUS_FILTERS.map(item => ({ id: item.id, label: item.label, count: counts[item.id] }))} />
          <SelectField label="Sort" value={sort} options={SORT_OPTIONS} onChange={onSort} end />
        </Toolbar>
        <p className="cfg-sr" role="status">{filtered ? `${plural(shown.length, "configuration")} of ${total} shown` : `${plural(total, "configuration")} shown`}</p>
        {shown.length
          ? <div className={`ci-grid${shown.length % 3 === 0 ? " ci-grid--fill3" : ""}${shown.length % 2 === 0 ? " ci-grid--fill2" : ""}`}>
            <ConfigCards>
              {shown.map(config => <IndexCard key={config.id} workspace={workspace} config={config} live={live} now={now} />)}
              <AddConfigurationTile href={routes.newConfiguration()} />
            </ConfigCards>
          </div>
          : <EmptyState title={noResultsTitle(search, status)}
            text={`Search matches configuration, robot, model, device and site names. Clear the filters to see all ${plural(total, "configuration")}.`}
            action={<button className="btn btn-secondary cfg-btn" type="button" onClick={clearFilters}>Clear filters</button>} />}
      </>}

    <ActivityFeed workspace={workspace} now={now} />
  </AppShell>;
}

/** Pause in typing before the search reaches the URL (and filters the cards). */
const SEARCH_DEBOUNCE_MS = 250;

/**
 * The search field (same markup as the kit's SearchField). It is uncontrolled so typing never waits for
 * the URL, which the router updates in a transition; the URL (and so the cards) follows once typing
 * pauses, not on every key. A change that comes from elsewhere (a link, Clear filters) is copied into it
 * while it is not being edited.
 */
function SearchBox({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    const node = input.current;
    if (node && node.value !== value && document.activeElement !== node) node.value = value;
  }, [value]);
  useEffect(() => () => { if (timer.current !== null) window.clearTimeout(timer.current); }, []);
  function type(next: string) {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { timer.current = null; onChange(next); }, SEARCH_DEBOUNCE_MS);
  }
  return <label className="cfg-field cfg-search">Search configurations
    <span className="cfg-search__box"><Icon name="search" /><input ref={input} className="field-input cfg-input" type="search" defaultValue={value} placeholder="Name, robot, model or device" onChange={event => type(event.target.value)} /></span>
  </label>;
}

function IndexCard({ workspace, config, live, now }: { workspace: ConvoyWorkspace; config: Configuration; live: LiveMap; now: number | null }) {
  const summary = configurationSummary(workspace, config, live, now);
  const attention = attentionCounts(attentionRobots(workspace, config.id, live, now));
  const verdict = cardVerdict(workspace, config);
  const run = summary.latestRun;
  return <ConfigCard href={routes.configuration(config.id)} name={config.name}
    badges={<ConfigStatusBadges configuration={config} />}
    note={verdict ? <VerdictNote verdict={verdict} now={now} /> : undefined}
    purpose={config.purpose}
    stack={[...stackRows(config), {
      label: "Latest eval",
      value: run
        ? <>{run.rev} · {fmtShare(successShare(run))} (n = {fmtCount(run.counts.episodes)})<span className="ci-eval"><RunStatus run={run} /><ProvenanceBadge provenance={run.provenance} now={now} /></span></>
        : <NotReported>No gated run yet</NotReported>,
    }]}
    foot={<div className="ci-foot-line">
      <span>{robotCountsLabel(workspace, config.id)}</span>
      {attention.attention > 0 && <Badge tone="warning" icon="flag" title={attention.detail}>{attention.attentionLabel}</Badge>}
      {attention.warning > 0 && <Badge tone="warning" title={attention.detail}>{attention.warningLabel}</Badge>}
    </div>}
    updated={`Updated ${now === null ? fmtDate(config.updatedAt) : fmtRelative(config.updatedAt, now)}`} />;
}

function VerdictNote({ verdict, now }: { verdict: CardVerdict; now: number | null }) {
  return <p className={`ci-note ci-note--${verdict.tone === "good" ? "good" : "warn"}`}>
    <Icon name={VERDICT_ICON[verdict.tone]} small />
    <span className="ci-note__body"><strong>{verdict.lead}</strong> {verdict.text}{verdict.provenance && <ProvenanceBadge provenance={verdict.provenance} now={now} />}</span>
  </p>;
}

function ActivityFeed({ workspace, now }: { workspace: ConvoyWorkspace; now: number | null }) {
  const events = recentActivity(workspace, {}, FEED_LIMIT);
  return <Panel eyebrow="Newest first · UTC · all configurations" title="Recent activity" titleId="ci-activity-title" className="cfg-section">
    {events.length
      ? <ol className="cfg-log ci-feed" aria-label="Recent activity, newest first">
        {events.map(event => {
          const kind = ACTIVITY_LABEL[event.kind];
          const subject = activitySubject(workspace, event);
          return <li key={event.id} className="cfg-log__row">
            <time className="cfg-log__time" dateTime={event.at}>{fmtDate(event.at)} {fmtTime(event.at, undefined, false)}</time>
            <span className="ci-feed__event"><Badge tone={kind.tone} icon={kind.flag ? "flag" : undefined}>{kind.label}</Badge></span>
            {subject.href ? <Link className="portal-table-link ci-feed__subject" href={subject.href}>{subject.label}</Link> : <span className="ci-feed__subject">{subject.label}</span>}
            <span className="cfg-log__msg">{event.message}</span>
            <span className="ci-feed__prov"><ProvenanceBadge provenance={event.provenance} now={now} /></span>
          </li>;
        })}
      </ol>
      : <p className="portal-empty">No activity has been recorded in this workspace yet.</p>}
  </Panel>;
}
