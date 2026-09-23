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
import { API_BASE_URL, CONSOLE_URL } from "@/lib/config";

export const metadata = { title: "API credentials" };

export default function CredentialsPage() {
  return (
    <>
      <DocHeader eyebrow="Reference" title="API credentials">
        A credential is a bearer token issued to one application. It is how your
        software authenticates to Memora, and it is the only thing that decides
        what that software can see.
      </DocHeader>

      <Callout tone="warn" title="Issued by a person, not by a program">
        Creating a credential needs a console session. An API credential cannot
        mint another one — otherwise a leaked token could quietly extend its own
        life. Issue them in{" "}
        <a href={CONSOLE_URL} className="text-accent hover:underline">
          the console
        </a>
        .
      </Callout>

      <Section title="Create a credential">
        <Endpoint method="POST" path="/api/v1/applications/{application_id}/credentials" />
        <Fields
          rows={[
            {
              name: "name",
              type: "string",
              required: true,
              description:
                "What this token is for, e.g. Production or CI. Shown in the console.",
            },
            {
              name: "expires_at",
              type: "string | null",
              description:
                "ISO-8601 timestamp. Omit for a token that does not expire.",
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `created = session.post(
    f"/applications/{application_id}/credentials",
    json={"name": "Production", "expires_at": None},
).json()

print(created["token"])  # the only time this exists`,
            js: `const created = await fetch(
  \`${API_BASE_URL}/api/v1/applications/\${applicationId}/credentials\`,
  {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: "Production", expires_at: null }),
  },
).then((r) => r.json());

console.log(created.token); // the only time this exists`,
          }}
        />
        <ResponseBlock
          status={201}
          json={`{
  "id": "0b0a0d2e-…",
  "application_id": "6f1c…",
  "name": "Production",
  "token": "memora_9f3c1d…",
  "token_preview": "memora_9f3c…",
  "status": "active",
  "created_at": "2026-09-22T17:10:55.002Z",
  "last_used_at": null,
  "expires_at": null,
  "warning": "Store this token securely. It will not be shown again."
}`}
        />
      </Section>

      <Section title="The token is shown once">
        <P>
          Memora stores a hash, not the token. The <C>token</C> field appears in
          this one response and in no other — every later read returns only{" "}
          <C>token_preview</C>, a fragment long enough to recognise a credential
          and too short to use.
        </P>
        <Ul>
          <li>Put it straight into your secret store or environment.</li>
          <li>
            If it is lost, revoke and reissue. There is no recovery endpoint
            because there is nothing to recover from.
          </li>
          <li>
            If it leaks, revoke immediately — revocation applies to the next
            request, with no cache to wait out.
          </li>
        </Ul>
      </Section>

      <Section title="List credentials">
        <Endpoint method="GET" path="/api/v1/credentials">
          Filter with <C>?application_id=</C>.
        </Endpoint>
        <Endpoint method="GET" path="/api/v1/applications/{application_id}/credentials" />
        <P>
          Each row carries <C>last_used_at</C>, which is the practical way to
          find a credential nothing is using any more before revoking it.
        </P>
      </Section>

      <Section title="Update a credential">
        <Endpoint method="PATCH" path="/api/v1/credentials/{credential_id}" />
        <Fields
          rows={[
            {
              name: "name",
              type: "string",
              description: "Rename it. Does not change the token.",
            },
            {
              name: "status",
              type: "string",
              description: (
                <>
                  <C>active</C> or <C>revoked</C>.
                </>
              ),
            },
          ]}
        />
      </Section>

      <Section title="Revoke a credential">
        <Endpoint method="DELETE" path="/api/v1/credentials/{credential_id}">
          Marks it revoked. The row stays, so the audit trail keeps its name and
          creation date; the token stops working.
        </Endpoint>
        <CodeTabs
          sample={{
            python: `session.delete(f"/credentials/{credential_id}")`,
            js: `await fetch(
  \`${API_BASE_URL}/api/v1/credentials/\${credentialId}\`,
  { method: "DELETE", credentials: "include" },
);`,
          }}
        />
      </Section>

      <Section title="What a credential can reach">
        <P>
          Exactly one application, inside exactly one tenant — the pair it was
          issued for. Ids in a URL can narrow that further but never widen it,
          and a request for anything outside returns <C>404</C>. See{" "}
          <Link href="/docs/authentication" className="text-accent hover:underline">
            Authentication
          </Link>
          .
        </P>
      </Section>
    </>
  );
}
