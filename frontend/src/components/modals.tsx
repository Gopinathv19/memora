"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import type { Actor, CredentialCreated } from "@/lib/types";
import { useMutation, useResource } from "@/lib/useResource";

import { Field, FileInput, Modal, Select, TextInput } from "./form";
import { Button, InlineError, Mono } from "./ui";

/**
 * The console's create forms.
 *
 * They live together rather than beside their pages because most are opened
 * from two or three places -- a credential is created from both the credentials
 * list and an application's detail page -- and a single copy keeps the wording
 * and validation identical wherever it appears.
 */

/** Slug suggestion matching the backend's lowercase-hyphenated rule. */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function Actions({
  onClose,
  pending,
  disabled,
  label,
  pendingLabel,
}: {
  onClose: () => void;
  pending: boolean;
  disabled: boolean;
  label: string;
  pendingLabel: string;
}) {
  return (
    <div className="flex justify-end gap-2 pt-1">
      <Button onClick={onClose} disabled={pending}>
        Cancel
      </Button>
      <Button type="submit" variant="primary" disabled={pending || disabled}>
        {pending ? pendingLabel : label}
      </Button>
    </div>
  );
}

/* -------------------------------------------------------------- Application */

export function CreateApplicationModal({
  tenantId,
  onClose,
  onCreated,
}: {
  tenantId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const { mutate, pending, error } = useMutation(api.applications.create);

  const effectiveSlug = slugEdited ? slug : slugify(name);

  return (
    <Modal
      title="Create application"
      description="A consuming system that uses Memora through an API credential."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await mutate(tenantId, { name, slug: effectiveSlug })) onCreated();
        }}
      >
        <Field label="Application name" required hint="For example, ThinkFill or LegalCase.">
          <TextInput value={name} onChange={setName} placeholder="ThinkFill" disabled={pending} required />
        </Field>
        <Field
          label="Slug"
          required
          hint="Lowercase words separated by hyphens. Unique within this tenant."
        >
          <TextInput
            value={effectiveSlug}
            onChange={(value) => {
              setSlugEdited(true);
              setSlug(value);
            }}
            placeholder="thinkfill"
            disabled={pending}
            required
          />
        </Field>
        {error && <InlineError message={error} />}
        <Actions
          onClose={onClose}
          pending={pending}
          disabled={!name.trim() || !effectiveSlug}
          label="Create application"
          pendingLabel="Creating…"
        />
      </form>
    </Modal>
  );
}

/* -------------------------------------------------------------------- Actor */

const ACTOR_TYPES = [
  { value: "user", label: "User — a human operator" },
  { value: "service", label: "Service — a service account" },
  { value: "agent", label: "Agent — an autonomous agent" },
  { value: "system", label: "System — a background process" },
];

export function CreateActorModal({
  applicationId,
  onClose,
  onCreated,
}: {
  applicationId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [externalId, setExternalId] = useState("");
  const [type, setType] = useState("user");
  const [name, setName] = useState("");
  const { mutate, pending, error } = useMutation(api.actors.create);

  return (
    <Modal
      title="Create actor"
      description="Whoever or whatever operates on Memora inside this application. Memora stores no credentials or profile for an actor — only your own identifier for it."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await mutate(applicationId, {
              external_id: externalId,
              type,
              name: name.trim() || null,
            })
          )
            onCreated();
        }}
      >
        <Field
          label="External ID"
          required
          hint="Your application's own identifier for this actor, e.g. lawyer_123 or usr_83921. Unique within the application."
        >
          <TextInput
            value={externalId}
            onChange={setExternalId}
            placeholder="lawyer_123"
            disabled={pending}
            required
          />
        </Field>
        <Field label="Type" hint="A subject can be opened by a person, a service, an agent or a system job.">
          <Select value={type} onChange={setType} options={ACTOR_TYPES} disabled={pending} />
        </Field>
        <Field label="Display name" hint="Optional. Shown in the console only.">
          <TextInput value={name} onChange={setName} placeholder="John Smith" disabled={pending} />
        </Field>
        {error && <InlineError message={error} />}
        <Actions
          onClose={onClose}
          pending={pending}
          disabled={!externalId.trim()}
          label="Create actor"
          pendingLabel="Creating…"
        />
      </form>
    </Modal>
  );
}

/* ------------------------------------------------------------------ Subject */

export function CreateSubjectModal({
  applicationId,
  onClose,
  onCreated,
}: {
  applicationId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [externalId, setExternalId] = useState("");
  const [actorId, setActorId] = useState("");
  const actors = useResource<Actor[]>(
    () => api.actors.list({ applicationId }),
    [applicationId],
  );
  const { mutate, pending, error } = useMutation(api.subjects.create);

  return (
    <Modal
      title="Create subject"
      description="A subject is a workspace: the boundary that sources are scoped to."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (
            await mutate(applicationId, {
              external_id: externalId,
              actor_id: actorId || null,
            })
          )
            onCreated();
        }}
      >
        <Field
          label="External ID"
          required
          hint="Your application's own workspace identifier, e.g. case-ABC-456 or customer_123. Unique within the application."
        >
          <TextInput
            value={externalId}
            onChange={setExternalId}
            placeholder="case-ABC-456"
            disabled={pending}
            required
          />
        </Field>
        <Field
          label="Originating actor"
          hint="Who caused this workspace to exist. Recorded for audit — it does not restrict who may work in it later."
        >
          <Select
            value={actorId}
            onChange={setActorId}
            disabled={pending || actors.loading}
            placeholder={
              actors.loading
                ? "Loading actors…"
                : actors.data?.length
                  ? "None"
                  : "No actors in this application yet"
            }
            options={(actors.data ?? []).map((actor) => ({
              value: actor.id,
              label: actor.name
                ? `${actor.external_id} — ${actor.name}`
                : actor.external_id,
            }))}
          />
        </Field>
        {error && <InlineError message={error} />}
        <Actions
          onClose={onClose}
          pending={pending}
          disabled={!externalId.trim()}
          label="Create subject"
          pendingLabel="Creating…"
        />
      </form>
    </Modal>
  );
}

/* --------------------------------------------------------------- Credential */

/**
 * Two-step: the form, then the raw token.
 *
 * Once the token is displayed, the form is gone and there is no way back to it
 * — mirroring the backend, where the token no longer exists after the response.
 */
export function CreateCredentialModal({
  applicationId,
  onClose,
  onCreated,
}: {
  applicationId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [issued, setIssued] = useState<CredentialCreated>();
  const { mutate, pending, error } = useMutation(api.credentials.create);

  if (issued) {
    return (
      <Modal
        title="Copy your API token now"
        description="This is the only time Memora will show it. Only a hash is stored, so it cannot be recovered — if you lose it, revoke this credential and create another."
        onClose={() => {
          onCreated();
          onClose();
        }}
        wide
      >
        <TokenReveal token={issued.token} name={issued.name} />
        <div className="mt-4 flex justify-end">
          <Button
            variant="primary"
            onClick={() => {
              onCreated();
              onClose();
            }}
          >
            I have copied the token
          </Button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal
      title="Create API credential"
      description="A bearer token this application uses to authenticate to Memora."
      onClose={onClose}
    >
      <form
        className="space-y-4"
        onSubmit={async (event) => {
          event.preventDefault();
          const created = await mutate(applicationId, {
            name,
            // datetime-local gives a local wall-clock string; the backend
            // parses ISO-8601, so send it as-is and let it apply the offset.
            expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
          });
          if (created) setIssued(created);
        }}
      >
        <Field label="Credential name" required hint="For example, Development or Production.">
          <TextInput
            value={name}
            onChange={setName}
            placeholder="Development"
            disabled={pending}
            required
          />
        </Field>
        <Field label="Expires at" hint="Optional. Leave empty for a token that does not expire.">
          <TextInput
            value={expiresAt}
            onChange={setExpiresAt}
            type="datetime-local"
            disabled={pending}
          />
        </Field>
        {error && <InlineError message={error} />}
        <Actions
          onClose={onClose}
          pending={pending}
          disabled={!name.trim()}
          label="Create credential"
          pendingLabel="Creating…"
        />
      </form>
    </Modal>
  );
}

function TokenReveal({ token, name }: { token: string; name: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="space-y-3">
      <div className="rounded border border-warn/30 bg-warn-soft p-3 text-sm text-ink">
        <strong className="font-medium">Store this token securely.</strong> It will
        not be shown again.
      </div>
      <div>
        <div className="mb-1 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
          {name}
        </div>
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface p-3">
          <code className="min-w-0 flex-1 font-mono text-[13px] break-all text-ink">
            {token}
          </code>
          <Button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(token);
                setCopied(true);
              } catch {
                // Clipboard access can be blocked; the token is selectable
                // on screen either way, so this is not worth an error banner.
                setCopied(false);
              }
            }}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      <p className="text-sm text-ink-secondary">
        Send it as{" "}
        <Mono>Authorization: Bearer {token.slice(0, 14)}…</Mono> on requests to
        the Memora API.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------- Source */

const SOURCE_TYPES = [
  { value: "url", label: "URL — a web page" },
  { value: "chat", label: "Chat — a conversation transcript" },
  { value: "file", label: "File — already in external storage" },
];

/**
 * Registering a source, two ways.
 *
 * "Upload a file" posts the bytes to Memora, which stores them and returns a
 * tracked source row. "Register metadata" records a source whose content lives
 * somewhere else (a URL, an S3 object, a chat transcript) without moving any
 * bytes. Both land in `pending`; neither extracts anything.
 */
export function CreateSourceModal({
  subjectId,
  applicationId,
  onClose,
  onCreated,
}: {
  subjectId: string;
  applicationId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [mode, setMode] = useState<"upload" | "metadata">("upload");
  const [file, setFile] = useState<File | null>(null);
  const [type, setType] = useState("url");
  const [filename, setFilename] = useState("");
  const [mimeType, setMimeType] = useState("");
  const [storageUri, setStorageUri] = useState("");
  const [actorId, setActorId] = useState("");

  const actors = useResource<Actor[]>(
    () => api.actors.list({ applicationId }),
    [applicationId],
  );
  const upload = useMutation(api.sources.upload);
  const register = useMutation(api.sources.create);

  const pending = upload.pending || register.pending;
  const error = upload.error ?? register.error;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const created =
      mode === "upload"
        ? file && (await upload.mutate(subjectId, file, actorId || null))
        : await register.mutate(subjectId, {
            type,
            filename: filename.trim() || null,
            mime_type: mimeType.trim() || null,
            storage_uri: storageUri.trim() || null,
            created_by_actor_id: actorId || null,
          });
    if (created) onCreated();
  };

  return (
    <Modal
      title="Register source"
      description="Memora records and tracks the source. Extraction, chunking and embeddings are a later phase — a new source stays pending."
      onClose={onClose}
      wide
    >
      <div className="mb-4 flex gap-1 rounded-md border border-line bg-surface p-1">
        {(
          [
            ["upload", "Upload a file"],
            ["metadata", "Register metadata"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setMode(value)}
            aria-pressed={mode === value}
            className={`flex-1 rounded-[4px] px-3 py-1.5 text-sm font-medium transition-colors ${
              mode === value
                ? "bg-panel text-ink shadow-sm"
                : "text-ink-secondary hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <form className="space-y-4" onSubmit={submit}>
        {mode === "upload" ? (
          <>
            <Field
              label="File"
              required
              hint="Posted to the backend, which stores the bytes and records the source. Maximum 50 MB."
            >
              <FileInput onChange={setFile} disabled={pending} />
            </Field>
            {file && (
              <p className="text-sm text-ink-secondary">
                Selected <span className="font-medium text-ink">{file.name}</span>{" "}
                — {file.type || "unknown type"}, {file.size.toLocaleString()} bytes
              </p>
            )}
          </>
        ) : (
          <>
            <Field label="Type" required>
              <Select value={type} onChange={setType} options={SOURCE_TYPES} disabled={pending} />
            </Field>
            <Field label="Filename or title" hint="For example, employee-handbook.pdf.">
              <TextInput
                value={filename}
                onChange={setFilename}
                placeholder="employee-handbook.pdf"
                disabled={pending}
              />
            </Field>
            <Field label="MIME type" hint="For example, application/pdf.">
              <TextInput
                value={mimeType}
                onChange={setMimeType}
                placeholder="application/pdf"
                disabled={pending}
              />
            </Field>
            <Field
              label="Storage URI"
              hint="Where the content already lives, e.g. s3://bucket/path/handbook.pdf or https://example.com/page."
            >
              <TextInput
                value={storageUri}
                onChange={setStorageUri}
                placeholder="s3://bucket/path/employee-handbook.pdf"
                disabled={pending}
              />
            </Field>
          </>
        )}

        <Field
          label="Registered by"
          hint="Optional audit record of which actor added this source."
        >
          <Select
            value={actorId}
            onChange={setActorId}
            disabled={pending || actors.loading}
            placeholder={actors.data?.length ? "None" : "No actors in this application"}
            options={(actors.data ?? []).map((actor) => ({
              value: actor.id,
              label: actor.name
                ? `${actor.external_id} — ${actor.name}`
                : actor.external_id,
            }))}
          />
        </Field>

        {error && <InlineError message={error} />}
        <Actions
          onClose={onClose}
          pending={pending}
          disabled={mode === "upload" ? !file : false}
          label={mode === "upload" ? "Upload and register" : "Register source"}
          pendingLabel={mode === "upload" ? "Uploading…" : "Registering…"}
        />
      </form>
    </Modal>
  );
}
