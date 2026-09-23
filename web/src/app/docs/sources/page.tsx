import {
  C,
  Callout,
  DocHeader,
  Endpoint,
  Fields,
  P,
  Section,
  Ul,
} from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL } from "@/lib/config";

export const metadata = { title: "Sources" };

export default function SourcesPage() {
  return (
    <>
      <DocHeader eyebrow="Ownership chain" title="Sources">
        A source is a registered piece of knowledge inside a subject: an
        uploaded file, a URL, a conversation. This is where day-to-day traffic
        goes.
      </DocHeader>

      <Callout title="Registering is not processing">
        Memora records the source and, for an upload, stores the bytes. A new
        source stays <C>pending</C> — extraction, chunking and embeddings are a
        later phase, and nothing currently advances it past that.
      </Callout>

      <Section title="Upload a file">
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources/upload">
          Multipart. Memora stores the bytes and records the source in one call.
          Maximum 50 MB.
        </Endpoint>
        <Fields
          title="Form fields"
          rows={[
            {
              name: "file",
              type: "file",
              required: true,
              description:
                "The bytes. Filename and content type are taken from the part itself.",
            },
            {
              name: "created_by_actor_id",
              type: "uuid | null",
              description:
                "Which actor registered it. Audit only; must belong to the same application.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `with open("employee-handbook.pdf", "rb") as handle:
    source = client.post(
        f"/subjects/{subject_id}/sources/upload",
        files={"file": ("employee-handbook.pdf", handle, "application/pdf")},
        data={"created_by_actor_id": actor_id},
    ).json()`,
            js: `const form = new FormData();
form.append("file", file, "employee-handbook.pdf");
form.append("created_by_actor_id", actorId);

// Do not set Content-Type by hand: the multipart boundary has to come
// from the runtime, and overriding it makes the body unparseable.
const source = await memora(\`/subjects/\${subjectId}/sources/upload\`, {
  method: "POST",
  body: form,
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/subjects/$SUBJECT_ID/sources/upload \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -F "file=@employee-handbook.pdf;type=application/pdf" \\
  -F "created_by_actor_id=$ACTOR_ID"`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "4b19…",
  "tenant_id": "a41e…",
  "application_id": "6f1c…",
  "subject_id": "9d77…",
  "created_by_actor_id": "c8d2…",
  "type": "file",
  "mime_type": "application/pdf",
  "filename": "employee-handbook.pdf",
  "storage_uri": "local://a41e…/9d77…/4b19….pdf",
  "size_bytes": 74449,
  "status": "pending",
  "created_at": "2026-09-22T17:04:38.913Z"
}`}
        />
      </Section>

      <Section title="Register something stored elsewhere">
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources">
          JSON. For content that already lives in your own storage, or for a URL
          or a conversation, where Memora tracks the reference rather than the
          bytes.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "type",
              type: "string",
              description: (
                <>
                  <C>file</C>, <C>url</C> or <C>chat</C>. Defaults to <C>file</C>
                  .
                </>
              ),
            },
            {
              name: "filename",
              type: "string | null",
              description: "Filename or title, e.g. employee-handbook.pdf.",
            },
            {
              name: "mime_type",
              type: "string | null",
              description: "For example application/pdf.",
            },
            {
              name: "storage_uri",
              type: "string | null",
              description:
                "Where the content already lives: s3://bucket/key, https://example.com/page.",
            },
            {
              name: "size_bytes",
              type: "integer | null",
              description: "Size, if you know it.",
            },
            {
              name: "created_by_actor_id",
              type: "uuid | null",
              description: "Which actor registered it. Audit only.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `source = client.post(
    f"/subjects/{subject_id}/sources",
    json={
        "type": "url",
        "filename": "Pricing page",
        "storage_uri": "https://example.com/pricing",
        "created_by_actor_id": actor_id,
    },
).json()`,
            js: `const source = await memora(\`/subjects/\${subjectId}/sources\`, {
  method: "POST",
  body: JSON.stringify({
    type: "url",
    filename: "Pricing page",
    storage_uri: "https://example.com/pricing",
    created_by_actor_id: actorId,
  }),
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/subjects/$SUBJECT_ID/sources \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"type":"url","storage_uri":"https://example.com/pricing"}'`,
          }}
        />
      </Section>

      <Section title="List sources">
        <Endpoint method="GET" path="/api/v1/sources">
          Filter with <C>?subject_id=</C>, <C>?application_id=</C>,{" "}
          <C>?tenant_id=</C> and <C>?status_filter=</C>.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/subjects/{subject_id}/sources">
          The same rows for one subject; also accepts <C>?status_filter=</C>.
        </Endpoint>
        <P>Status is one of:</P>
        <Ul>
          <li>
            <C>pending</C> — registered, nothing done to it yet. Where everything
            currently sits.
          </li>
          <li>
            <C>processing</C> — being worked on.
          </li>
          <li>
            <C>completed</C> — processed.
          </li>
          <li>
            <C>failed</C> — processing gave up.
          </li>
        </Ul>
        <CodeTabs
          sample={{
            python: `client.get(
    "/sources",
    params={"subject_id": subject_id, "status_filter": "pending"},
).json()`,
            js: `await memora(
  \`/sources?subject_id=\${subjectId}&status_filter=pending\`,
);`,
            curl: `curl -s "${API_BASE_URL}/api/v1/sources?subject_id=$SUBJECT_ID&status_filter=pending" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
      </Section>

      <Section title="Get, update and delete">
        <Endpoint method="GET" path="/api/v1/sources/{source_id}" />
        <Endpoint
          method="GET"
          path="/api/v1/subjects/{subject_id}/sources/{source_id}"
        >
          The same row, reached through its subject.
        </Endpoint>
        <Endpoint method="PATCH" path="/api/v1/sources/{source_id}" />
        <Endpoint method="DELETE" path="/api/v1/sources/{source_id}">
          Removes the source. For an uploaded file, the stored bytes go with it.
        </Endpoint>
      </Section>

      <Section title="Move a source">
        <Endpoint method="POST" path="/api/v1/sources/{source_id}/move">
          Moves the source to another subject (folder) in the same
          application. The <C>storage_uri</C> never changes — moving a file is
          one row update, not a byte copy.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "target_subject_id",
              type: "uuid",
              required: true,
              description:
                "The subject to move the source into. Must belong to the same application as the source.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `source = client.post(
    f"/sources/{source_id}/move",
    json={"target_subject_id": folder_id},
).json()`,
            js: `const source = await memora(\`/sources/\${sourceId}/move\`, {
  method: "POST",
  body: JSON.stringify({ target_subject_id: folderId }),
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/sources/$SOURCE_ID/move \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"target_subject_id":"'$FOLDER_ID'"}'`,
          }}
        />
      </Section>

      <Section title="Download the bytes">
        <Endpoint method="GET" path="/api/v1/sources/{source_id}/content">
          Streams the stored file back. Only meaningful for sources that were
          uploaded — one registered by <C>storage_uri</C> has no bytes here.
        </Endpoint>
        <CodeTabs
          sample={{
            python: `response = client.get(f"/sources/{source_id}/content")
with open("downloaded.pdf", "wb") as handle:
    handle.write(response.content)`,
            js: `const response = await fetch(
  \`${API_BASE_URL}/api/v1/sources/\${sourceId}/content\`,
  { headers: { Authorization: \`Bearer \${TOKEN}\` } },
);
const blob = await response.blob();`,
            curl: `curl -s ${API_BASE_URL}/api/v1/sources/$SOURCE_ID/content \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -o downloaded.pdf`,
          }}
        />
      </Section>
    </>
  );
}
