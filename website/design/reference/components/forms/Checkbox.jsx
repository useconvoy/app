import React from "react";

export function Checkbox({ label, className = "", ...rest }) {
  return (
    <label className={`cv-check ${className}`.trim()}>
      <input type="checkbox" {...rest} />
      <span>{label}</span>
    </label>
  );
}
