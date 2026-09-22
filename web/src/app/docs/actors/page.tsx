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

export const metadata = { title: "Actors" };

export default function ActorsPage() {
  return (
    <>
      <DocHeader eyebrow="Ownership chain" title="Actors">
        An actor is whoever or whatever operates on Memora inside an
        application. It exists so that a workspace and a source can record who
        caused them — not so that Memora can authenticate anyone.
      </DocHeader>

      <Callout title="Memora is not an identity provider">
        An actor stores your own identifier, a type and an optional display
        name. No email, no password, no role, no session. Authentication of your
        users stays entirely on your side.
      </Callout>

      <Section title="Create an actor">
        <Endpoint method="POST" path="/api/v1/applications/{application_id}/actors" />
        <Fields
          rows={[
            {
              name: "external_id",
              type: "string",
              required: true,
              description:
                "Your own identifier for this actor, e.g. lawyer_123 or usr_83921. Unique within the application.",
            },
            {
              name: "type",
              type: "string",
              description: (
                <>
                  <C>user</C>, <C>service</C>, <C>agent</C> or <C>system</C>.
                  Defaults to <C>user</C>.
                </>
              ),
            },
            {
              name: "name",
              type: "string | null",
              description: "Optional display name, shown in the console only.",
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
            python: `actor = client.post(
    f"/applications/{application_id}/actors",
    json={"external_id": "lawyer_123", "type": "user", "name": "John Smith"},
).json()`,
            js: `const actor = await memora(\`/applications/\${applicationId}/actors\`, {
  method: "POST",
  body: JSON.stringify({
    external_id: "lawyer_123",
    type: "user",
    name: "John Smith",
  }),
});`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/applications/$APPLICATION_ID/actors \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"external_id":"lawyer_123","type":"user","name":"John Smith"}'`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "c8d2…",
  "tenant_id": "a41e…",
  "application_id": "6f1c…",
  "external_id": "lawyer_123",
  "type": "user",
  "name": "John Smith",
  "status": "active",
  "created_at": "2026-09-22T17:06:02.110Z",
  "application_name": "ThinkFill",
  "subject_count": 0
}`}
        />
      </Section>

      <Section title="Why type is broader than “user”">
        <P>
          A workspace can be opened by a background job or an autonomous agent
          as easily as by a person. Forcing those through a <C>user</C> type
          would make the audit trail say something untrue, so the four types are
          first-class:
        </P>
        <Ul>
          <li>
            <C>user</C> — a human operating your application.
          </li>
          <li>
            <C>service</C> — another system of yours acting on its own.
          </li>
          <li>
            <C>agent</C> — an autonomous process making its own decisions.
          </li>
          <li>
            <C>system</C> — scheduled or internal work with no initiator.
          </li>
        </Ul>
      </Section>

      <Section title="List actors">
        <Endpoint method="GET" path="/api/v1/actors">
          Filter with <C>?application_id=</C> and <C>?tenant_id=</C>.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/applications/{application_id}/actors" />
        <CodeTabs
          sample={{
            python: `client.get("/actors", params={"application_id": application_id}).json()`,
            js: `await memora(\`/actors?application_id=\${applicationId}\`);`,
            curl: `curl -s "${API_BASE_URL}/api/v1/actors?application_id=$APPLICATION_ID" \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
      </Section>

      <Section title="Get and update">
        <Endpoint method="GET" path="/api/v1/actors/{actor_id}" />
        <Endpoint method="PATCH" path="/api/v1/actors/{actor_id}" />
        <Fields
          rows={[
            { name: "name", type: "string", description: "New display name." },
            {
              name: "status",
              type: "string",
              description: (
                <>
                  <C>active</C> or <C>suspended</C>. Suspending an actor does not
                  touch the subjects it opened — the audit record stands.
                </>
              ),
            },
          ]}
        />
      </Section>
    </>
  );
}
