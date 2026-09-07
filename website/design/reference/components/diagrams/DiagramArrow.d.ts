/** Connector glyph for Convoy diagrams. Solid = execution, dashed (oxide) = release/config/evidence, dotted (muted) = optional placement. */
export interface DiagramArrowProps {
  kind?: "execution" | "release" | "optional";
  label?: string;
}
export declare function DiagramArrow(props: DiagramArrowProps): JSX.Element;
/** Inline legend for the three line kinds. */
export declare function DiagramLegend(props: { items?: ("execution" | "release" | "optional")[] }): JSX.Element;
