import Link from "next/link";

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

export const metadata = { title: "Subjects" };

export default function SubjectsPage() {
  return (
    <>
      <DocHeader eyebrow="Ownership chain" title="Subjects">
        A subject is a workspace: the boundary sources are scoped to. A legal
        case, a customer, a project, a conversation — whatever unit your
        application groups knowledge by. A subject can also be a{" "}
        <C>folder</C> inside another subject, to any depth.
      </DocHeader>

      <Callout title="Folders are subjects">
        There is no separate folder resource. A subject with a{" "}
        <C>parent_subject_id</C> is a folder; a subject without one is a root
        workspace. Sources, scope checks and the console all treat both the
        same way, and <C>external_id</C> plays the role of a folder name —
        unique among its siblings, like a filename in a directory.
      </Callout>

      <Section title="Create a subject">
        <Endpoint method="POST" path="/api/v1/applications/{application_id}/subjects" />
        <Fields
          rows={[
            {
              name: "external_id",
              type: "string",
              required: true,
              description:
                "Your own workspace or folder identifier, e.g. case-ABC-456 or Contracts. Unique among siblings: no other root (or child of the same parent) in this application may share it.",
            },
            {
              name: "parent_subject_id",
              type: "uuid | null",
              description:
                "The subject this one lives inside, when it is a folder. Omitted for a root workspace. Must belong to the same application.",
            },
            {
              name: "actor_id",
              type: "uuid | null",
              description:
                "The actor that caused this workspace to exist. Recorded for audit; it does not restrict who may work in it later.",
            },
            {
              name: "status",
              type: "string",
              description: (
                <>
                  <C>active</C> or <C>suspended</C>. Defaults to <C>active</C>.
                </>
              ),
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `subject = client.post(
    f"/applications/{application_id}/subjects",
    json={"external_id": "case-ABC-456", "actor_id": actor["id"]},
).json()

# A folder inside it:
folder = client.post(
    f"/applications/{application_id}/subjects",
    json={"external_id": "Contracts", "parent_subject_id": subject["id"]},
).json()`,
            js: `const subject = await memora(\`/applications/\${applicationId}/subjects\`, {
  method: "POST",
  body: JSON.stringify({
    external_id: "case-ABC-456",
    actor_id: actor.id,
  }),
});

// A folder inside it:
const folder = await memora(\`/applications/\${applicationId}/subjects\`, {
  method: "POST",
  body: JSON.stringify({
    external_id: "Contracts",
    parent_subject_id: subject.id,
  }),
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/applications/$APPLICATION_ID/subjects \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"Contracts","parent_subject_id":"'$SUBJECT_ID'"}'`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "9d77…",
  "tenant_id": "a41e…",
  "application_id": "6f1c…",
  "actor_id": "c8d2…",
  "parent_subject_id": "5e02…",
  "external_id": "Contracts",
  "status": "active",
  "created_at": "2026-09-22T17:08:44.207Z",
  "application_name": "ThinkFill",
  "tenant_name": "Pranav Tools",
  "actor_external_id": "lawyer_123",
  "source_count": 0,
  "child_count": 0,
  "path": [
    {"id": "5e02…", "external_id": "case-ABC-456"}
  ]
}`}
        />
        <Callout title="The actor must belong to the same application">
          Passing an <C>actor_id</C> from another application is rejected with{" "}
          <C>422 validation_error</C>. The same rule applies to{" "}
          <C>parent_subject_id</C>. Ownership is checked, not assumed.
        </Callout>
      </Section>

      <Section title="List subjects">
        <Endpoint method="GET" path="/api/v1/subjects">
          Filter with <C>?application_id=</C>, <C>?tenant_id=</C>,{" "}
          <C>?actor_id=</C>, <C>?parent_subject_id=</C> or <C>?roots_only=</C>.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/applications/{application_id}/subjects" />
        <Ul>
          <li>
            <C>?parent_subject_id=</C> — the children of one folder. This is
            how a file browser lists a directory.
          </li>
          <li>
            <C>?roots_only=true</C> — only root workspaces, not folders inside
            them.
          </li>
          <li>
            Neither given — every subject in scope, folders included.
          </li>
        </Ul>
        <CodeTabs
          sample={{
            python: `# The folders directly inside a subject:
children = client.get(
    "/subjects", params={"parent_subject_id": subject_id}
).json()`,
            js: `const children = await memora(
  \`/subjects?parent_subject_id=\${subjectId}\`,
);`,
            curl: `curl -s "${API_BASE_URL}/api/v1/subjects?parent_subject_id=$SUBJECT_ID" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <P>
          Each row carries <C>source_count</C> and <C>child_count</C>, so a
          folder list needs no second call to show how much is in each one.
        </P>
      </Section>

      <Section title="Get and update">
        <Endpoint method="GET" path="/api/v1/subjects/{subject_id}">
          The detail view also returns <C>path</C> — the ancestor chain,
          nearest parent first — for building breadcrumbs.
        </Endpoint>
        <Endpoint method="PATCH" path="/api/v1/subjects/{subject_id}" />
        <Fields
          rows={[
            {
              name: "external_id",
              type: "string",
              description:
                "Rename. Must stay unique among the subject's siblings.",
            },
            {
              name: "parent_subject_id",
              type: "uuid | null",
              description:
                "Move the subject (and its whole subtree) under a new parent. An explicit null moves it back to the root. Rejected if the target is the subject itself or one of its descendants.",
            },
            {
              name: "status",
              type: "string",
              description: (
                <>
                  <C>active</C> or <C>suspended</C>.
                </>
              ),
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `# Move a folder under a new parent:
client.patch(
    f"/subjects/{folder_id}",
    json={"parent_subject_id": new_parent_id},
)`,
            js: `await memora(\`/subjects/\${folderId}\`, {
  method: "PATCH",
  body: JSON.stringify({ parent_subject_id: newParentId }),
});`,
            curl: `curl -s -X PATCH \\
  ${API_BASE_URL}/api/v1/subjects/$FOLDER_ID \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"parent_subject_id":"'$NEW_PARENT_ID'"}'`,
          }}
        />
      </Section>

      <Section title="Delete a subject">
        <Endpoint method="DELETE" path="/api/v1/subjects/{subject_id}">
          Deletes the subject, its whole folder subtree, and every source in
          it — including any bytes Memora stored for those sources.
        </Endpoint>
        <P>
          This is a large destructive operation; the rows go first and the
          stored bytes after, so a failed cleanup leaves a stray object rather
          than a row pointing at bytes that are gone.
        </P>
      </Section>

      <Section title="Sources inside a subject">
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources" />
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources/upload" />
        <Endpoint method="GET" path="/api/v1/subjects/{subject_id}/sources" />
        <Endpoint
          method="GET"
          path="/api/v1/subjects/{subject_id}/sources/{source_id}"
        />
        <Endpoint method="POST" path="/api/v1/sources/{source_id}/move">
          Moves a source to another subject (folder) in the same application.
          One row update — no bytes are copied.
        </Endpoint>
        <P>
          All five are covered in{" "}
          <Link href="/docs/sources" className="text-accent hover:underline">
            Sources
          </Link>
          .
        </P>
      </Section>
    </>
  );
}
