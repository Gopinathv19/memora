import Link from "next/link";

import {
  C,
  Callout,
  DocHeader,
  Endpoint,
  Fields,
  P,
  Section,
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
        application groups knowledge by.
      </DocHeader>

      <Section title="Create a subject">
        <Endpoint method="POST" path="/api/v1/applications/{application_id}/subjects" />
        <Fields
          rows={[
            {
              name: "external_id",
              type: "string",
              required: true,
              description:
                "Your own workspace identifier, e.g. case-ABC-456 or customer_123. Unique within the application.",
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
).json()`,
            js: `const subject = await memora(\`/applications/\${applicationId}/subjects\`, {
  method: "POST",
  body: JSON.stringify({
    external_id: "case-ABC-456",
    actor_id: actor.id,
  }),
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/applications/$APPLICATION_ID/subjects \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"case-ABC-456","actor_id":"'$ACTOR_ID'"}'`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "9d77…",
  "tenant_id": "a41e…",
  "application_id": "6f1c…",
  "actor_id": "c8d2…",
  "external_id": "case-ABC-456",
  "status": "active",
  "created_at": "2026-09-22T17:08:44.207Z",
  "application_name": "ThinkFill",
  "tenant_name": "Pranav Tools",
  "actor_external_id": "lawyer_123",
  "source_count": 0
}`}
        />
        <Callout title="The actor must belong to the same application">
          Passing an <C>actor_id</C> from another application is rejected with{" "}
          <C>422 validation_error</C>. Ownership is checked, not assumed.
        </Callout>
      </Section>

      <Section title="List subjects">
        <Endpoint method="GET" path="/api/v1/subjects">
          Filter with <C>?application_id=</C>, <C>?tenant_id=</C> or{" "}
          <C>?actor_id=</C>.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/applications/{application_id}/subjects" />
        <CodeTabs
          sample={{
            python: `client.get("/subjects", params={"application_id": application_id}).json()`,
            js: `await memora(\`/subjects?application_id=\${applicationId}\`);`,
            curl: `curl -s "${API_BASE_URL}/api/v1/subjects?actor_id=$ACTOR_ID" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <P>
          Each row carries <C>source_count</C>, so a workspace list needs no
          second call to show how much is in each one.
        </P>
      </Section>

      <Section title="Get and update">
        <Endpoint method="GET" path="/api/v1/subjects/{subject_id}" />
        <Endpoint method="PATCH" path="/api/v1/subjects/{subject_id}" />
        <Fields
          rows={[
            {
              name: "external_id",
              type: "string",
              description: "New identifier. Still unique within the application.",
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
      </Section>

      <Section title="Sources inside a subject">
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources" />
        <Endpoint method="POST" path="/api/v1/subjects/{subject_id}/sources/upload" />
        <Endpoint method="GET" path="/api/v1/subjects/{subject_id}/sources" />
        <Endpoint
          method="GET"
          path="/api/v1/subjects/{subject_id}/sources/{source_id}"
        />
        <P>
          All four are covered in{" "}
          <Link href="/docs/sources" className="text-accent hover:underline">
            Sources
          </Link>
          .
        </P>
      </Section>
    </>
  );
}
