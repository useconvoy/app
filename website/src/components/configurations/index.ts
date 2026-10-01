/**
 * Configurations UI kit: React versions of the cookbook components (exact cfg-* /
 * portal-* markup; CSS in src/styles/configurations.css).
 *
 * Page pattern (each route already renders one of ./pages/*):
 *
 *   const ws = useWorkspace();                         // @/lib/configurations/client
 *   const now = useNow();                              // wall clock for "Measured · 9s ago"
 *   const live = useLiveRobots(robots);                // measured data for robots with deviceId
 *   if (ws.status === "loading") return <AppShell crumbs={…}><LoadingState /></AppShell>;
 *   return <AppShell crumbs={[{ label: "Configurations", href: routes.index() }, { label: name }]}>
 *     <PageHeader eyebrow="Configuration" title={name} badges={…} actions={…} meta={fmtUpdated(at)} />
 *     <WorkspaceNotice workspace={ws.workspace} />     // one notice, first on every page: source (sample + Import), provenance, device status
 *     <Section title="…" note="…">…</Section>
 *   </AppShell>;
 *
 * Rules the components already follow, and pages must keep:
 * - Every metric shows exactly one <ProvenanceBadge>; missing values use <NotReported> ("Not reported", never 0).
 * - Measured values come only from live bindings (`useLiveRobots` + `robotReadings`); never plot measured
 *   and sample series together or aggregate them.
 * - One oxide primary action per view (`btn btn-primary cfg-btn`), last in the actions row; `cfg-btn` on every button.
 * - Navigation is a Next <Link>; actions are <button type="button">. A ConfigCard is one link with no controls inside.
 * - Drawers and tabs live in the URL: `useQueryTab(tabs)` for `?tab=`, `useQueryState("trace" | "rollout")` for drawers.
 * - Writes go through `save(current => mutation(current, …, Date.now()))` with the helpers in
 *   @/lib/configurations/mutations; disable write actions when `!ws.canSave`.
 * - Page-specific styles go in your page's CSS file (configurations-{index,dashboard,robot,eval}.css, already
 *   imported) with a page prefix; page-only components live next to your page, not in this index.
 */
export { AppShell, Breadcrumbs, type Crumb, type NavKey } from "./AppShell";
export {
  Badge, Badges, CONFIG_STATUS_LABEL, ConfigStatus, ConfigStatusBadges, HEALTH_LABEL, HealthBadge, NotReported, OUTCOME_LABEL, OutcomeBadge,
  PATH_LABEL, PathChip, ProvenanceBadge, ROLE_LABEL, RoleBadge, RUN_STATUS_LABEL, RunStatus, Tags, Version, type BadgeTone,
} from "./Badges";
export { autoDomain, BandPlot, Legend, LegendKey, MiniCard, plotGeometry, PlotPair, SmallMultiples, sparkGeometry, Sparkline, type LegendItem, type PlotRow, type SeriesName } from "./Charts";
export { CompatList, CompatRow, VERDICT_LABEL } from "./Compat";
export { AddConfigurationTile, ConfigCard, ConfigCards } from "./ConfigCard";
export { DataTable, type Column, type SortState } from "./DataTable";
export { DeviceBoard, Fact, FactList, FactPanel, HealthStrip, Panel, Section, type FactItem } from "./Facts";
export { FailureBars } from "./FailureBars";
export { useNow, useQueryState } from "./hooks";
export { ConvoyMark, Icon, ICON_PATHS, type IconName } from "./Icons";
export { Delta, KpiGrid, KpiTile } from "./Kpi";
export { LiveDeviceProvider, useLiveRefresh, useLiveRobot, useLiveRobots } from "./LiveDeviceProvider";
export { LogPanel } from "./LogPanel";
export { ImportWorkspaceButton, Notice, WorkspaceNotice, type NoticeTone } from "./Notice";
export { Drawer, Modal, useDialog } from "./Overlay";
export { PageHeader } from "./PageHeader";
export { ProgressBar } from "./ProgressBar";
export { ConfigurationsRoot, useSession, WorkspaceSession, type WorkspaceSessionValue } from "./Session";
export { Meter, SliceRow, SliceTable, weakestSlices, type SliceRowData } from "./Slices";
export { EmptyState, LoadingState, NotFoundState, PagePlaceholder } from "./States";
export { Stepper, type StepItem, type StepState } from "./Stepper";
export { LinkTabs, TabPanel, Tabs, useQueryTab, type TabItem } from "./Tabs";
export { FilterChips, SearchField, SelectField, Toolbar, type ChipOption } from "./Toolbar";
export { niceAxisEnd, Waterfall } from "./Waterfall";
