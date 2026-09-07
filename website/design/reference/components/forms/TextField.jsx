import React from "react";

let idCounter = 0;
export function useFieldId(id) {
  const ref = React.useRef(id || `cv-f-${++idCounter}`);
  return ref.current;
}

export function Field({ id, label, optional = false, hint, error, children }) {
  return (
    <div className="cv-field">
      <label className="cv-field__label" htmlFor={id}>{label}{optional && <span className="cv-field__optional">Optional</span>}</label>
      {children}
      {hint && !error && <div className="cv-field__hint" id={`${id}-hint`}>{hint}</div>}
      {error && <div className="cv-field__error" id={`${id}-err`} role="alert">{error}</div>}
    </div>
  );
}

export function TextField({ id, label, optional, hint, error, className = "", ...rest }) {
  const fid = useFieldId(id);
  const describedBy = error ? `${fid}-err` : hint ? `${fid}-hint` : undefined;
  return (
    <Field id={fid} label={label} optional={optional} hint={hint} error={error}>
      <input id={fid} className={`cv-input ${className}`.trim()} aria-invalid={error ? "true" : undefined} aria-describedby={describedBy} required={!optional} {...rest} />
    </Field>
  );
}
