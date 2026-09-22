"use client";

import { useEffect, useId, useRef, useState } from "react";
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

/**
 * A dropdown, built rather than borrowed.
 *
 * A native `<select>` renders its option list through the operating system, so
 * the highlighted row comes out in the platform's selection colour -- a blue
 * bar that no stylesheet can reach. Everything else in the console is themed,
 * so the list is rebuilt here as ordinary elements: a button that opens a
 * listbox, with the keyboard behaviour a native select would have given us for
 * free (arrows to move, Enter to choose, Escape to dismiss, Home/End to jump).
 *
 * The props are deliberately identical to the native version it replaced, so
 * every call site kept working unchanged.
 */
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
  /** When given, an empty-valued first entry -- "All tenants", "None". */
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();

  const entries =
    placeholder !== undefined
      ? [{ value: "", label: placeholder }, ...options]
      : options;
  const selectedIndex = entries.findIndex((entry) => entry.value === value);
  const selected = selectedIndex >= 0 ? entries[selectedIndex] : undefined;

  // An outside click means the person's attention has moved on.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  function openList() {
    if (disabled) return;
    setActive(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  }

  function choose(index: number) {
    const entry = entries[index];
    if (!entry) return;
    onChange(entry.value);
    setOpen(false);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        openList();
      }
      return;
    }
    switch (event.key) {
      case "Escape":
        event.preventDefault();
        setOpen(false);
        break;
      case "ArrowDown":
        event.preventDefault();
        setActive((index) => Math.min(index + 1, entries.length - 1));
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive((index) => Math.max(index - 1, 0));
        break;
      case "Home":
        event.preventDefault();
        setActive(0);
        break;
      case "End":
        event.preventDefault();
        setActive(entries.length - 1);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        choose(active);
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  const empty = entries.length === 0;

  return (
    <div className="relative" ref={root}>
      <button
        type="button"
        disabled={disabled || empty}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-haspopup="listbox"
        className={`${FIELD_CLASS} flex items-center justify-between gap-2 text-left ${
          open ? "border-brand bg-panel ring-2 ring-brand/25" : ""
        }`}
      >
        <span
          className={`truncate ${selected && selected.value !== "" ? "text-ink" : "text-ink-tertiary"}`}
        >
          {empty ? "Nothing to choose" : (selected?.label ?? placeholder ?? "")}
        </span>
        <svg
          aria-hidden
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={`size-3.5 shrink-0 text-ink-tertiary transition-transform ${open ? "rotate-180" : ""}`}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>

      {open && (
        <ul
          id={listId}
          role="listbox"
          tabIndex={-1}
          className="absolute z-50 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-line bg-panel py-1 shadow-lg"
        >
          {entries.map((entry, index) => {
            const isSelected = entry.value === value;
            return (
              <li
                key={entry.value || "__placeholder"}
                role="option"
                aria-selected={isSelected}
                onMouseEnter={() => setActive(index)}
                onMouseDown={(event) => {
                  // mousedown, not click: the outside-click handler fires first
                  // on click and would close the list before the choice lands.
                  event.preventDefault();
                  choose(index);
                }}
                className={`cursor-pointer px-3 py-1.5 text-sm ${
                  isSelected
                    ? "bg-accent-soft font-medium text-accent"
                    : index === active
                      ? "bg-surface-strong text-ink"
                      : "text-ink-secondary"
                }`}
              >
                {entry.label}
              </li>
            );
          })}
        </ul>
      )}
    </div>
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
        className={`w-full rounded-lg border border-line bg-panel shadow-2xl ${wide ? "max-w-2xl" : "max-w-lg"}`}
      >
        <header className="flex items-start justify-between gap-4 rounded-t-lg border-b border-line bg-surface px-4 py-3">
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
