"use client";

import type { ReactNode } from "react";

/*
 * Supabase's inputs sit on a faintly grey fill with a hairline border, and
 * signal focus with a green ring rather than a colour change on the border
 * alone -- that ring is the only strong colour a form ever shows.
 */
const FIELD_CLASS =
  "w-full rounded-md border border-line bg-surface px-3 py-[7px] text-sm text-ink placeholder:text-ink-tertiary transition-colors focus:border-brand focus:bg-panel focus:outline-none focus:ring-2 focus:ring-brand/25 disabled:bg-surface-strong disabled:text-ink-secondary";

export function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-ink">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </span>
      {children}
      {hint && (
        <span className="mt-1.5 block text-xs text-ink-secondary">{hint}</span>
      )}
    </label>
  );
}

export function TextInput({
  value,
  onChange,
  placeholder,
  disabled,
  type = "text",
  required,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  type?: string;
  required?: boolean;
}) {
  return (
    <input
      type={type}
      value={value}
      required={required}
      disabled={disabled}
      placeholder={placeholder}
      onChange={(event) => onChange(event.target.value)}
      className={FIELD_CLASS}
    />
  );
}

export function Select({
  value,
  onChange,
  options,
  disabled,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
  placeholder?: string;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      className={FIELD_CLASS}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}

export function FileInput({
  onChange,
  disabled,
  accept,
}: {
  onChange: (file: File | null) => void;
  disabled?: boolean;
  accept?: string;
}) {
  return (
    <input
      type="file"
      accept={accept}
      disabled={disabled}
      onChange={(event) => onChange(event.target.files?.[0] ?? null)}
      className="w-full rounded-md border border-dashed border-line bg-surface px-3 py-2.5 text-sm text-ink file:mr-3 file:rounded-md file:border file:border-line file:bg-panel file:px-2.5 file:py-1 file:text-sm file:font-medium file:text-ink hover:file:bg-surface-strong"
    />
  );
}

/** A modal dialog. Used for every create form in the console. */
export function Modal({
  title,
  description,
  onClose,
  children,
  wide,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 p-4 backdrop-blur-[2px] sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      // Clicking the backdrop dismisses; clicks inside the panel must not.
      onClick={onClose}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className={`w-full overflow-hidden rounded-lg border border-line bg-panel shadow-2xl ${wide ? "max-w-2xl" : "max-w-lg"}`}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line bg-surface px-4 py-3">
          <div>
            <h2 className="text-sm font-medium text-ink">{title}</h2>
            {description && (
              <p className="mt-0.5 text-xs text-ink-secondary">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-lg leading-none text-ink-secondary hover:bg-surface-strong hover:text-ink"
          >
            ×
          </button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
