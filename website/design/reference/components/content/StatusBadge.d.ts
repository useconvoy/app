/** Mono status chip. Used to mark stage ("In development"), proposed scope ("Proposed workflow"), and preview states — never marketing labels. */
export interface StatusBadgeProps {
  tone?: "neutral" | "info" | "success" | "warning" | "error" | "accent" | "inverse";
  /** Leading 8px dot in the current colour. Default true. */
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}
export declare function StatusBadge(props: StatusBadgeProps): JSX.Element;
