import Link from "next/link";

import { C, Callout, DocHeader, Endpoint, P, Section, Ul } from "@/components/docs";
import { CodeTabs, ResponseBlock } from "@/components/CodeTabs";
import { API_BASE_URL, CONSOLE_URL } from "@/lib/config";

export const metadata = { title: "Authentication" };

export default function AuthenticationPage() {
  return (
    <>
      <DocHeader eyebrow="Getting started" title="Authentication">
        Memora authenticates two kinds of caller and keeps them strictly apart.
        Which one you are determines not just whether a request is allowed, but
        how much of the database it can see.
      </DocHeader>

      <Section title="API credentials">
        <P>
          A credential is a bearer token issued to one application. Send it on
          every request:
        </P>
        <CodeTabs
          sample={{
            python: `headers = {"Authorization": f"Bearer {os.environ['MEMORA_TOKEN']}"}`,
            js: `const headers = { Authorization: \`Bearer \${process.env.MEMORA_TOKEN}\` };`,
            curl: `-H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
        />
        <P>
          Every token begins with <C>memora_</C>. That prefix is not decoration:
          it is how Memora tells a machine credential from a session token, so a
          leaked API token can never be replayed as a browser session, and vice
          versa.
        </P>
        <Callout title="A credential sees one application, inside one tenant">
          Its reach is derived from the credential row itself, never from the
          request. Asking for another tenant&apos;s application returns{" "}
          <C>404 not_found</C> — the same answer as for something that does not
          exist, because from that token&apos;s perspective it does not.
        </Callout>
      </Section>

      <Section title="Console sessions">
        <P>
          A signed-in person carries an HttpOnly session cookie and reaches every
          tenant they own. This is what{" "}
          <a href={CONSOLE_URL} className="text-accent hover:underline">
            the console
          </a>{" "}
          uses, and what creating tenants and issuing credentials requires — an
          API credential cannot mint another credential.
        </P>
        <Ul>
          <li>
            <C>POST /auth/signup</C> — email, password, name.
          </li>
          <li>
            <C>POST /auth/login</C> — email, password.
          </li>
          <li>
            <C>POST /auth/google</C> — a Google ID token.
          </li>
          <li>
            <C>GET /auth/me</C> — the current user, or <C>401</C>.
          </li>
          <li>
            <C>POST /auth/logout</C> — clears the cookie.
          </li>
        </Ul>
        <P>
          For a script that cannot hold cookies, the session token may also be
          sent as a bearer value — anything without the <C>memora_</C> prefix is
          tried as a session and never as a credential.
        </P>
      </Section>

      <Section title="Confirming a token">
        <Endpoint method="GET" path="/api/v1/whoami">
          Resolve the presented credential to its credential, application and
          tenant. The quickest check that an environment is configured right.
        </Endpoint>
        <CodeTabs
          sample={{
            python: `client.get("/whoami").json()`,
            js: `await memora("/whoami");`,
            curl: `curl -s ${API_BASE_URL}/api/v1/whoami \\
  -H "Authorization: Bearer $MEMORA_TOKEN"`,
          }}
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

      <Section title="When authentication fails">
        <P>
          A missing, malformed, revoked or expired token gets <C>401</C> with{" "}
          <C>code: &quot;unauthenticated&quot;</C>. A token that is valid but
          asking for something outside its scope gets <C>404</C>, not <C>403</C>
          — Memora does not confirm that a resource exists to a caller that
          cannot see it. See{" "}
          <Link href="/docs/errors" className="text-accent hover:underline">
            Errors
          </Link>
          .
        </P>
      </Section>

      <Section title="Handling a lost token">
        <P>
          Tokens are stored as hashes, so no endpoint can return one after
          creation. Revoke the credential — <C>DELETE /credentials/{"{id}"}</C> —
          and issue a replacement. Revocation takes effect on the next request;
          there is no cache to wait out. See{" "}
          <Link href="/docs/credentials" className="text-accent hover:underline">
            API credentials
          </Link>
          .
        </P>
      </Section>
    </>
  );
}
