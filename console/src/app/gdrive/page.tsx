"use client";

/**
 * The Google Drive connector page.
 *
 * Flow: status → (connect) → list PDFs → import into a subject → the
 * existing RAG pipeline takes over (extraction → chunks → embeddings →
 * graph), and "Ask Memora" answers questions about the imported files.
 *
 * The OAuth redirect lands back here with ?connected=1 (or ?error=consent).
 */

import { useCallback, useEffect, useState } from "react";

import { PageHeader, Panel, StatusBadge } from "@/components/ui";
import { GoogleDriveButton } from "@/connectors/google-drive/GoogleDriveButton";
import { GoogleDriveFiles } from "@/connectors/google-drive/GoogleDriveFiles";
import { GoogleDrivePrivateAsk } from "@/connectors/google-drive/GoogleDrivePrivateAsk";
import { googleDriveApi } from "@/connectors/google-drive/googleDriveApi";
import type { DriveFile, DriveStatus } from "@/connectors/google-drive/googleDriveTypes";

export default function GoogleDrivePage() {
  const [status, setStatus] = useState<DriveStatus | null>(null);
  const [files, setFiles] = useState<DriveFile[] | null>(null);
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const s = await googleDriveApi.status();
      setStatus(s);
      if (s.connected) {
        setFiles(await googleDriveApi.files());
      } else {
        setFiles(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load Drive status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // The OAuth callback redirects to /gdrive?connected=1 -- clean the
    // query string up so a refresh does not repeat the message.
    const params = new URLSearchParams(window.location.search);
    if (params.has("connected") || params.has("error")) {
      window.history.replaceState({}, "", "/gdrive");
    }
    reload();
  }, [reload]);

  const connected = status?.connected ?? false;

  return (
    <>
      <PageHeader
        eyebrow="Connector"
        title="Google Drive"
        description="Connect your Google Drive and pick PDFs to work with. Ask privately reads them in memory and stores nothing; Import adds them to the persistent knowledge base. Read-only access either way."
      />

      {loading && !status ? (
        <Panel>
          <div className="px-4 py-10 text-center text-sm text-ink-secondary">Loading…</div>
        </Panel>
      ) : status && !status.configured ? (
        <Panel title="Not configured">
          <div className="px-4 py-6 text-sm text-ink-secondary">
            The backend has no Google Drive OAuth client. Set GOOGLE_CLIENT_ID,
            GOOGLE_DRIVE_CLIENT_SECRET and GOOGLE_REDIRECT_URI in backend/.env, then
            restart it.
          </div>
        </Panel>
      ) : (
        <Panel
          title="Connection"
          description={
            connected
              ? `Connected as ${status?.connection?.drive_account_email ?? "unknown"} (read-only).`
              : "Not connected. You will be redirected to Google to grant read-only Drive access."
          }
          actions={
            connected ? (
              <div className="flex items-center gap-2">
                <StatusBadge status="active" />
                <button
                  type="button"
                  onClick={async () => {
                    await googleDriveApi.disconnect();
                    reload();
                  }}
                  className="text-xs text-ink-tertiary underline-offset-2 hover:text-ink hover:underline"
                >
                  Disconnect
                </button>
              </div>
            ) : undefined
          }
        >
          <div className="px-4 py-4">
            {!connected && <GoogleDriveButton />}
          </div>
        </Panel>
      )}

      {error && (
        <div className="mt-5">
          <Panel>
            <div className="px-4 py-3 text-sm text-red-600">{error}</div>
          </Panel>
        </div>
      )}

      {connected && files && files.length > 0 && (
        <div className="mt-5">
          <Panel
            title="Ask without importing (private mode)"
            description="Zero-retention Q&A: the selected PDFs are read in memory and discarded with the answer. No copy, chunks or embeddings are stored."
          >
            <GoogleDrivePrivateAsk files={files} />
          </Panel>
        </div>
      )}

      {connected && files && (
        <div className="mt-5">
          <Panel
            title="Import into the knowledge base"
            description="Persistent mode: the file is downloaded server-side and fed into the same pipeline as an upload, so Ask Memora can answer about it later."
            counter={files.length}
          >
            <GoogleDriveFiles files={files} />
          </Panel>
        </div>
      )}
    </>
  );
}