import Link from "next/link";

import { C, Callout, DocHeader, P, Section } from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL, CONSOLE_URL } from "@/lib/config";

export const metadata = { title: "Quickstart" };

export default function QuickstartPage() {
  return (
    <>
      <DocHeader eyebrow="Getting started" title="Quickstart">
        From an empty account to an answered question. The first three steps
        happen once, in the console; the rest is what your application does
        every day.
      </DocHeader>

      <Section title="1. Create a tenant and an application">
        <P>
          Sign in at{" "}
          <a href={CONSOLE_URL} className="text-accent hover:underline">
            the console
          </a>
          , create a tenant, then create an application inside it. Both are also
          API calls — see{" "}
          <Link href="/docs/tenants" className="text-accent hover:underline">
            Tenants
          </Link>{" "}
          — but they need a console session, so the console is the shorter path.
        </P>
      </Section>

      <Section title="2. Issue an API credential">
        <P>
          On the application, create a credential. The raw token is shown once
          and never again; only a hash is stored. Copy it into your
          application&apos;s environment as <C>MEMORA_TOKEN</C>.
        </P>
        <ResponseBlock
          status={201}
          json={`{
  "id": "0b0a0d2e-…",
  "application_id": "6f1c…",
  "name": "Production",
  "token": "memora_9f3c1d…",
  "token_preview": "memora_9f3c…",
  "status": "active",
  "warning": "Store this token securely. It will not be shown again."
}`}
        />
        <Callout tone="warn" title="This is a one-way door">
          If the token is lost, revoke the credential and issue another. There is
          no endpoint that returns it again, because Memora does not have it.
        </Callout>
      </Section>

      <Section title="3. Confirm the token resolves">
        <P>
          <C>GET /whoami</C> answers what a token is: which credential, which
          application, which tenant. It is the fastest way to prove your
          environment is wired up correctly.
        </P>
        <CodeTabs
          sample={{
            python: `import os
import httpx

BASE = "${API_BASE_URL}/api/v1"
TOKEN = os.environ["MEMORA_TOKEN"]

client = httpx.Client(
    base_url=BASE,
    headers={"Authorization": f"Bearer {TOKEN}"},
)

print(client.get("/whoami").json())`,
            js: `const BASE = "${API_BASE_URL}/api/v1";
const TOKEN = process.env.MEMORA_TOKEN;

async function memora(path, init = {}) {
  const response = await fetch(BASE + path, {
    ...init,
    headers: {
      Authorization: \`Bearer \${TOKEN}\`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const { detail, code } = await response.json();
    throw new Error(\`\${response.status} \${code}: \${detail}\`);
  }
  return response.status === 204 ? null : response.json();
}

console.log(await memora("/whoami"));`,
            curl: `curl -s ${API_BASE_URL}/api/v1/whoami \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
          caption="The `memora()` helper is reused by every JavaScript sample on this site."
        />
        <ResponseBlock
          json={`{
  "credential_id": "0b0a0d2e-…",
  "credential_name": "Production",
  "application_id": "6f1c…",
  "application_name": "ThinkFill",
  "application_slug": "thinkfill",
  "tenant_id": "a41e…",
  "tenant_name": "Pranav Tools"
}`}
        />
      </Section>

      <Section title="4. Create an actor">
        <P>
          An actor is your own identifier for whoever is operating — Memora
          stores no profile and no credentials for it. Create one the first time
          you see a given <C>external_id</C>.
        </P>
        <CodeTabs
          sample={{
            python: `application_id = client.get("/whoami").json()["application_id"]

actor = client.post(
    f"/applications/{application_id}/actors",
    json={"external_id": "lawyer_123", "type": "user", "name": "John Smith"},
).json()`,
            js: `const { application_id } = await memora("/whoami");

const actor = await memora(\`/applications/\${application_id}/actors\`, {
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
      </Section>

      <Section title="5. Open a subject">
        <P>
          A subject is the workspace sources live in — a case, a customer, a
          project. <C>actor_id</C> records who caused it to exist; it does not
          restrict who may work in it later.
        </P>
        <CodeTabs
          sample={{
            python: `subject = client.post(
    f"/applications/{application_id}/subjects",
    json={"external_id": "case-ABC-456", "actor_id": actor["id"]},
).json()`,
            js: `const subject = await memora(\`/applications/\${application_id}/subjects\`, {
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
      </Section>

      <Section title="6. Upload a source">
        <P>
          Upload the file with <C>extract=true</C>. Memora stores it and
          returns at once with the source in <C>processing</C>. In the
          background it extracts the text, chunks it and embeds the chunks.
          To send text instead of a file, use{" "}
          <C>POST /subjects/{"{subject_id}"}/sources/text</C>, which accepts
          the same <C>extract</C> flag.
        </P>
        <CodeTabs
          sample={{
            python: `with open("employee-handbook.pdf", "rb") as handle:
    source = client.post(
        f"/subjects/{subject['id']}/sources/upload",
        files={"file": ("employee-handbook.pdf", handle, "application/pdf")},
        data={"created_by_actor_id": actor["id"], "extract": "true"},
    ).json()

print(source["id"], source["status"])  # … processing`,
            js: `const form = new FormData();
form.append("file", file, "employee-handbook.pdf");
form.append("created_by_actor_id", actor.id);
form.append("extract", "true");

const source = await memora(\`/subjects/\${subject.id}/sources/upload\`, {
  method: "POST",
  body: form, // no Content-Type: the browser sets the multipart boundary
});

console.log(source.id, source.status); // … processing`,
            curl: `curl -s -X POST \\
  ${API_BASE_URL}/api/v1/subjects/$SUBJECT_ID/sources/upload \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -F "file=@employee-handbook.pdf;type=application/pdf" \\
  -F "created_by_actor_id=$ACTOR_ID" \\
  -F "extract=true"`,
          }}
        />
      </Section>

      <Section title="7. Ask a question">
        <P>
          Once the embedding coverage reaches 100%, the source can answer
          questions. Coverage is reported by{" "}
          <C>GET /embedding/sources/{"{source_id}"}/status</C>. Narrow the
          query to the subject so that only this workspace&apos;s sources are
          searched.
        </P>
        <CodeTabs
          sample={{
            python: `import time

while client.get(f"/embedding/sources/{source['id']}/status").json()["coverage_percent"] < 100:
    time.sleep(3)

result = client.post(
    "/query",
    json={"query": "How many days of annual leave do employees get?",
          "subject_id": subject["id"]},
).json()

print(result["answer"])
for chunk in result["chunks"]:
    print(" -", chunk["source_filename"], "p.", chunk["page_start"])`,
            js: `let status;
do {
  await new Promise((r) => setTimeout(r, 3000));
  status = await memora(\`/embedding/sources/\${source.id}/status\`);
} while (status.coverage_percent < 100);

const result = await memora("/query", {
  method: "POST",
  body: JSON.stringify({
    query: "How many days of annual leave do employees get?",
    subject_id: subject.id,
  }),
});

console.log(result.answer);`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/query \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query":"How many days of annual leave do employees get?","subject_id":"'$SUBJECT_ID'"}'`,
          }}
        />
        <P>
          That is the whole loop. The answer comes back with the chunks it was
          written from, including file name, section and page. See{" "}
          <Link href="/docs/retrieval" className="text-accent hover:underline">
            Retrieval
          </Link>{" "}
          for the options, and{" "}
          <Link href="/docs/sources" className="text-accent hover:underline">
            Sources
          </Link>{" "}
          for listing, filtering and downloading.
        </P>
      </Section>
    </>
  );
}
