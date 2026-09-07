import React from "react";
import { Field, useFieldId } from "./TextField.jsx";

export function TextArea({ id, label, optional, hint, error, rows = 5, className = "", ...rest }) {
  const fid = useFieldId(id);
  const describedBy = error ? `${fid}-err` : hint ? `${fid}-hint` : undefined;
  return (
    <Field id={fid} label={label} optional={optional} hint={hint} error={error}>
      <textarea id={fid} rows={rows} className={`cv-textarea ${className}`.trim()} aria-invalid={error ? "true" : undefined} aria-describedby={describedBy} required={!optional} {...rest} />
    </Field>
  );
}
