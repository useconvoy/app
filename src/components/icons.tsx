// Inline 16px stroke icons (Lucide-style), currentColor, no external deps.

function I({ children, size = 16 }: { children: React.ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export const IconGrid = ({ size }: { size?: number }) => (
  <I size={size}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></I>
);
export const IconLayers = ({ size }: { size?: number }) => (
  <I size={size}><path d="M12 2 2 7l10 5 10-5-10-5Z" /><path d="m2 17 10 5 10-5" /><path d="m2 12 10 5 10-5" /></I>
);
export const IconBot = ({ size }: { size?: number }) => (
  <I size={size}><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 8V4" /><circle cx="12" cy="3" r="1" /><path d="M9 13v2M15 13v2" /></I>
);
export const IconPlay = ({ size }: { size?: number }) => (
  <I size={size}><circle cx="12" cy="12" r="9" /><path d="m10 8.5 5 3.5-5 3.5v-7Z" /></I>
);
export const IconShieldCheck = ({ size }: { size?: number }) => (
  <I size={size}><path d="M12 22s8-3.5 8-10V5l-8-3-8 3v7c0 6.5 8 10 8 10Z" /><path d="m9 11.5 2 2 4-4" /></I>
);
export const IconList = ({ size }: { size?: number }) => (
  <I size={size}><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01" strokeWidth="2.4" /></I>
);
export const IconPlug = ({ size }: { size?: number }) => (
  <I size={size}><path d="M12 22v-3" /><path d="M7 13v-3a5 5 0 0 1 10 0v3" /><path d="M5 13h14l-1.5 4a4 4 0 0 1-3.8 2.8h-3.4A4 4 0 0 1 6.5 17L5 13Z" /><path d="M9 5V2M15 5V2" /></I>
);
export const IconCheck = ({ size }: { size?: number }) => (
  <I size={size}><path d="m4.5 12.5 5 5 10-11" /></I>
);
export const IconX = ({ size }: { size?: number }) => (
  <I size={size}><path d="m6 6 12 12M18 6 6 18" /></I>
);
export const IconFlag = ({ size }: { size?: number }) => (
  <I size={size}><path d="M5 21V4" /><path d="M5 4h13l-2.5 4L18 12H5" /></I>
);
export const IconHand = ({ size }: { size?: number }) => (
  <I size={size}><path d="M17 11V6a1.5 1.5 0 0 0-3 0M14 10V4.5a1.5 1.5 0 0 0-3 0V10M11 10.5V6a1.5 1.5 0 0 0-3 0v8" /><path d="M17 11a1.5 1.5 0 0 1 3 0v3a7 7 0 0 1-7 7h-1.5c-2.2 0-3.7-.8-4.9-2.4L4 15.1a1.6 1.6 0 0 1 2.5-2L8 15" /></I>
);
export const IconArrowUpRight = ({ size }: { size?: number }) => (
  <I size={size}><path d="M7 17 17 7" /><path d="M8 7h9v9" /></I>
);
export const IconPause = ({ size }: { size?: number }) => (
  <I size={size}><circle cx="12" cy="12" r="9" /><path d="M10 9v6M14 9v6" /></I>
);
export const IconDoc = ({ size }: { size?: number }) => (
  <I size={size}><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-5Z" /><path d="M14 2v5h5" /></I>
);
export const IconMail = ({ size }: { size?: number }) => (
  <I size={size}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></I>
);
export const IconChat = ({ size }: { size?: number }) => (
  <I size={size}><path d="M21 12a8 8 0 0 1-8 8H4l2.3-2.9A8 8 0 1 1 21 12Z" /></I>
);
export const IconAlert = ({ size }: { size?: number }) => (
  <I size={size}><path d="M12 3 2.5 20h19L12 3Z" /><path d="M12 10v4" /><path d="M12 17.5h.01" strokeWidth="2.4" /></I>
);
export const IconPlus = ({ size }: { size?: number }) => (
  <I size={size}><path d="M12 5v14M5 12h14" /></I>
);
export const IconSpinner = ({ size }: { size?: number }) => (
  <svg width={size ?? 16} height={size ?? 16} viewBox="0 0 24 24" fill="none" aria-hidden="true" style={{ animation: "spin 0.8s linear infinite" }}>
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2.5" />
    <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
  </svg>
);
