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

export const metadata = { title: "Retrieval" };

const QUERY_RESPONSE_JSON = `{
  "query": "What is the notice period for early termination?",
  "answer": "Either party may terminate the agreement early with 30 days written notice, as stated in section 8.2 of the master service agreement.",
  "strategy_id": "3f1c…",
  "strategy_name": "Memora Dense v1",
  "chunks": [
    {
      "chunk_id": "7d2a…",
      "source_id": "4b19…",
      "subject_id": "c88f…",
      "content": "8.2 Early termination. Either party may terminate this agreement …",
      "content_type": "text",
      "section_path": ["Master Service Agreement", "Termination"],
      "page_start": 4,
      "page_end": 4,
      "token_count": 312,
      "similarity": 0.8341,
      "rerank_score": 6.42
    }
  ],
  "retrieval": {
    "hnsw_candidates": 50,
    "mmr_candidates": 10,
    "final_chunks": 5,
    "reranker_used": true
  },
  "usage": { "prompt_tokens": 2140, "completion_tokens": 96, "latency_ms": 1830 }
}`;

export default function RetrievalPage() {
  return (
    <>
      <DocHeader eyebrow="Processing" title="Retrieval">
        Ask Memora a question and get a grounded answer with its evidence.
        One synchronous, read-only call — <C>POST /query</C> — runs the full
        pipeline: the question is embedded, the closest chunks are found,
        refined, and handed to a language model that answers using only
        them. A query can only ever see chunks the caller owns.
      </DocHeader>

      <Callout title="The answer is grounded, never invented">
        The answering model is instructed to use only the retrieved chunks.
        If the available knowledge does not contain the answer, it says so
        plainly instead of guessing. Chunks are evidence, not instructions —
        a document that says "ignore previous instructions" cannot
        hijack the answer.
      </Callout>

      <Section title="The four-stage pipeline">
        <Ul>
          <li>
            <strong>1. Vector search.</strong> The question is embedded under
            the same strategy as the stored vectors, and an HNSW index finds
            the ~50 nearest chunks by cosine similarity. Fast and broad —
            the next stages refine.
          </li>
          <li>
            <strong>2. Diversification (MMR).</strong> Vector search often
            returns several copies of the same paragraph. Maximal Marginal
            Relevance re-selects 10 candidates that are both relevant and
            <em> different</em>, so the evidence covers more of the document.
          </li>
          <li>
            <strong>3. Reranking.</strong> A dedicated cross-encoder reads the
            question and each candidate <em>together</em> and scores how well
            it actually answers — strictly more accurate than comparing two
            independently computed vectors. The final 5 chunks come from
            this score. If the reranking service is down, the pipeline falls
            back to the MMR order rather than failing the question.
          </li>
          <li>
            <strong>4. Grounded answer.</strong> The 5 chunks become the
            context for the answering model, which writes the answer with
            each chunk's section and page available for provenance.
          </li>
        </Ul>
        <P>
          Every response carries its own trace — how many candidates each
          stage produced and whether the reranker was used — plus per-chunk{" "}
          <C>similarity</C> and <C>rerank_score</C>, so quality is measurable
          without guesswork.
        </P>
      </Section>

      <Section title="Ask a question">
        <Endpoint method="POST" path="/api/v1/query">
          Answer a question from the caller's embedded chunks. The scope
          decides what knowledge exists to answer from: a console user
          queries every tenant they own, a credential queries its own
          application. Optional <C>subject_id</C>/<C>source_id</C> narrow the
          search further.
        </Endpoint>
        <Fields
          rows={[
            {
              name: "query",
              type: "string ≤ 4000",
              required: true,
              description: <>The question.</>,
            },
            {
              name: "subject_id",
              type: "uuid",
              description: (
                <>Narrow the search to one subject. Outside scope → 404.</>
              ),
            },
            {
              name: "source_id",
              type: "uuid",
              description: (
                <>Narrow the search to one source. Outside scope → 404.</>
              ),
            },
            {
              name: "strategy_id",
              type: "uuid",
              description: (
                <>
                  Query under a non-active strategy — for A/B evaluation
                  against an alternative embedding configuration. See{" "}
                  <C>Embeddings</C>.
                </>
              ),
            },
          ]}
        />
        <CodeTabs
          sample={{
            python: `answer = client.post("/query", json={
    "query": "What is the notice period for early termination?",
    "subject_id": subject_id,
}).json()
# answer["answer"], answer["chunks"], answer["retrieval"]`,
            js: `const answer = await memora("/query", {
  method: "POST",
  body: JSON.stringify({
    query: "What is the notice period for early termination?",
    subject_id: subjectId,
  }),
});`,
            curl: `curl -s -X POST ${API_BASE_URL}/api/v1/query \\
  -H "Authorization: Bearer $MEMORA_TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{"query": "What is the notice period for early termination?"}'`,
          }}
        />
        <ResponseBlock json={QUERY_RESPONSE_JSON} />

        <P>The response fields:</P>
        <Ul>
          <li>
            <C>answer</C> — the grounded answer, written from the evidence
            only.
          </li>
          <li>
            <C>chunks[]</C> — the evidence, best first: content, section
            path, page range, and both stage scores (<C>similarity</C> from
            the vector search, <C>rerank_score</C> from the cross-encoder —{" "}
            <C>null</C> when the fallback was used).
          </li>
          <li>
            <C>retrieval</C> — the pipeline trace: <C>hnsw_candidates</C>,{" "}
            <C>mmr_candidates</C>, <C>final_chunks</C>,{" "}
            <C>reranker_used</C>.
          </li>
          <li>
            <C>usage</C> — the answering model's prompt/completion tokens and
            latency, for cost attribution.
          </li>
          <li>
            <C>strategy_id</C> / <C>strategy_name</C> — which strategy's
            vector space was searched.
          </li>
        </Ul>

        <P>
          When nothing in scope is embedded yet, the answer says so plainly
          and <C>retrieval</C> reports zeros — no guessing. Check{" "}
          <C>GET /embedding/stats</C> to see what is searchable.
        </P>
      </Section>

      <Section title="Errors">
        <Ul>
          <li>
            <C>422</C>: the query is empty or longer than 4,000 characters.
          </li>
          <li>
            <C>404</C>: a <C>subject_id</C> or <C>source_id</C> outside the
            caller's scope — the same "outside scope does not
            exist" rule the rest of the API follows.
          </li>
        </Ul>
      </Section>
    </>
  );
}
