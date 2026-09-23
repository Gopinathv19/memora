import { C, Callout, DocHeader, P, Section } from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";

export const metadata = { title: "Errors" };

const CODES = [
  {
    status: 400,
    code: "bad_request",
    when: "The request was malformed in a way the more specific codes do not cover.",
  },
  {
    status: 401,
    code: "unauthenticated",
    when: "No token, a malformed one, or one that has been revoked or has expired.",
  },
  {
    status: 403,
    code: "forbidden",
    when: "Authenticated, but the action itself is not allowed for this kind of caller.",
  },
  {
    status: 404,
    code: "not_found",
    when: "The resource does not exist — or exists outside what this token can see. The two are deliberately indistinguishable.",
  },
  {
    status: 409,
    code: "conflict",
    when: "A uniqueness rule was violated: a duplicate slug in a tenant, a duplicate external_id in an application.",
  },
  {
    status: 422,
    code: "validation_error",
    when: "The body parsed but a value was unacceptable — a missing required field, or an id pointing outside this application.",
  },
];

export default function ErrorsPage() {
  return (
    <>
      <DocHeader eyebrow="Reference" title="Errors">
        Every failure has the same shape, whatever raised it, so one handler in
        your client covers all of them.
      </DocHeader>

      <Section title="The response shape">
        <ResponseBlock
          status={409}
          json={`{
  "detail": "An application with this slug already exists in the tenant",
  "code": "conflict"
}`}
        />
        <P>
          <C>detail</C> is a sentence written to be shown to whoever triggered
          the call. <C>code</C> is the stable machine-readable string — branch on
          it, not on the wording of <C>detail</C>, which may be reworded.
        </P>
      </Section>

      <Section title="Status codes">
        <div className="my-4 overflow-hidden rounded-lg border border-line">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line bg-surface text-left">
                <th className="px-4 py-2 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
                  Status
                </th>
                <th className="px-4 py-2 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
                  Code
                </th>
                <th className="px-4 py-2 text-xs font-medium uppercase tracking-wide text-ink-tertiary">
                  When
                </th>
              </tr>
            </thead>
            <tbody>
              {CODES.map((row) => (
                <tr key={row.code} className="border-b border-line-soft last:border-0">
                  <td className="px-4 py-2.5 align-top font-mono text-xs text-ink">
                    {row.status}
                  </td>
                  <td className="px-4 py-2.5 align-top font-mono text-xs text-ink">
                    {row.code}
                  </td>
                  <td className="px-4 py-2.5 align-top text-ink-secondary">
                    {row.when}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <Callout title="404 rather than 403 for scope">
          When a token asks for a resource belonging to another tenant, Memora
          answers <C>404</C>. A <C>403</C> would confirm the resource exists,
          which is information the caller has not earned.
        </Callout>
      </Section>

      <Section title="Handling them">
        <P>
          Read <C>code</C>, not the status alone: <C>409</C> on a create usually
          means &quot;already done&quot; and is safe to treat as success in a
          setup script, while <C>422</C> on the same call means the input is
          wrong and retrying will not help.
        </P>
        <CodeTabs
          sample={{
            python: `import httpx

try:
    application = client.post(
        f"/tenants/{tenant_id}/applications",
        json={"name": "ThinkFill", "slug": "thinkfill"},
    )
    application.raise_for_status()
except httpx.HTTPStatusError as error:
    body = error.response.json()
    if body["code"] == "conflict":
        # Already created by an earlier run -- fetch it instead.
        application = client.get(
            "/applications", params={"tenant_id": tenant_id}
        )
    else:
        raise RuntimeError(f"{body['code']}: {body['detail']}") from error`,
            js: `class MemoraError extends Error {
  constructor(status, code, detail) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

async function memora(path, init = {}) {
  const response = await fetch(BASE + path, {
    ...init,
    headers: {
      Authorization: \`Bearer \${TOKEN}\`,
      ...(init.body && typeof init.body === "string"
        ? { "Content-Type": "application/json" }
        : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    const { detail, code } = await response.json();
    throw new MemoraError(response.status, code, detail);
  }
  return response.status === 204 ? null : response.json();
}`,
          }}
          caption="Branch on `code`; `detail` is for humans."
        />
      </Section>

      <Section title="Rate limits">
        <P>
          There are none yet. Do not build a client that assumes unlimited
          throughput — a retry with backoff on <C>5xx</C> is worth having
          regardless, and it is what you will already need when limits arrive.
        </P>
      </Section>
    </>
  );
}
