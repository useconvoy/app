import { ImageResponse } from "next/og";

// Generated at build time from the live design tokens, so the social card can
// never drift from the site the way a checked-in PNG does. No network fetch and
// no font download — the image builds offline with the runtime's default face.

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt =
  "Convoy Labs — the control plane for company agents. One tool call, evaluated in Sandbox and Production.";

const INK = "#14171b";
const PAPER = "#fafaf8";
const BORDER = "#e4e3dd";
const ACCENT = "#1b4dc1";
const MUTED = "#494d55";
const FAINT = "#686c74";

function Verdict({
  env,
  label,
  tone,
}: {
  env: string;
  label: string;
  tone: { fg: string; bg: string; border: string };
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        padding: "20px 22px",
        gap: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          fontSize: 15,
          fontWeight: 700,
          letterSpacing: 1.4,
          color: FAINT,
        }}
      >
        {env.toUpperCase()}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "10px 14px",
          borderRadius: 8,
          fontSize: 22,
          fontWeight: 700,
          color: tone.fg,
          background: tone.bg,
          border: `1px solid ${tone.border}`,
        }}
      >
        {label}
      </div>
    </div>
  );
}

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: PAPER,
          padding: 64,
          fontFamily: "Arial, sans-serif",
          color: INK,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 11,
              background: ACCENT,
              color: "#fff",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 24,
              fontWeight: 700,
            }}
          >
            C
          </div>
          <div style={{ display: "flex", fontSize: 26, fontWeight: 700, letterSpacing: -0.4 }}>
            Convoy Labs
          </div>
          <div
            style={{
              display: "flex",
              marginLeft: 12,
              paddingLeft: 16,
              borderLeft: `1px solid ${BORDER}`,
              fontSize: 20,
              color: FAINT,
            }}
          >
            The control plane for company agents
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div
            style={{
              display: "flex",
              fontSize: 60,
              fontWeight: 700,
              lineHeight: 1.1,
              letterSpacing: -2.2,
              maxWidth: 940,
            }}
          >
            An agent can reason about the work. It shouldn&rsquo;t decide what it&rsquo;s allowed to
            touch.
          </div>
          <div style={{ display: "flex", fontSize: 24, color: MUTED, maxWidth: 880, lineHeight: 1.45 }}>
            The environment grants the tools. The gateway checks every call, injects the
            credentials, and records the verdict.
          </div>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            background: "#ffffff",
            border: `1px solid ${BORDER}`,
            borderRadius: 14,
          }}
        >
          <div
            style={{
              display: "flex",
              padding: "14px 22px",
              borderBottom: `1px solid ${BORDER}`,
              fontSize: 21,
              color: MUTED,
            }}
          >
            email.send → ap@northwindsystems.com
          </div>
          <div style={{ display: "flex" }}>
            <Verdict
              env="Sandbox"
              label="Refused"
              tone={{ fg: "#a52218", bg: "#fdf1ef", border: "#f3c5bf" }}
            />
            <div style={{ display: "flex", width: 1, background: BORDER }} />
            <Verdict
              env="Production"
              label="Held for approval"
              tone={{ fg: "#8a4b08", bg: "#fcf5e8", border: "#efd5a0" }}
            />
          </div>
        </div>
      </div>
    ),
    size,
  );
}
