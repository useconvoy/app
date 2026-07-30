import { ImageResponse } from "next/og";

export const size = { width: 64, height: 64 };
export const contentType = "image/png";

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          borderRadius: 16,
          background: "#4f46e5",
          color: "#ffffff",
          fontSize: 34,
          fontWeight: 750,
          fontFamily: "Arial, sans-serif",
        }}
      >
        C
      </div>
    ),
    size
  );
}
