"use client";

import type { ReactNode } from "react";

const FIELD_CLASS =
  "w-full rounded border border-line bg-panel px-2.5 py-1.5 text-sm text-ink placeholder:text-ink-tertiary focus:border-accent focus:outline-none disabled:bg-muted-soft disabled:text-ink-secondary";

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
      <span className="mb-1 block text-sm font-bold text-ink">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-secondary">{hint}</span>}
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
      className="w-full rounded border border-dashed border-line bg-muted-soft/50 px-2.5 py-2 text-sm text-ink file:mr-3 file:rounded file:border file:border-accent file:bg-panel file:px-2.5 file:py-1 file:text-sm file:font-bold file:text-accent hover:file:bg-accent-soft"
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
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/45 p-4 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      // Clicking the backdrop dismisses; clicks inside the panel must not.
      onClick={onClose}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className={`w-full rounded-lg border border-line bg-panel shadow-xl ${wide ? "max-w-2xl" : "max-w-lg"}`}
      >
        <header className="flex items-start justify-between gap-4 border-b border-line-soft px-4 py-3">
          <div>
            <h2 className="text-base font-bold text-ink">{title}</h2>
            {description && (
              <p className="mt-0.5 text-sm text-ink-secondary">{description}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-lg leading-none text-ink-secondary hover:bg-muted-soft hover:text-ink"
          >
            ×
          </button>
        </header>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
