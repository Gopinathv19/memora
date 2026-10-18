import { redirect } from "next/navigation";

// The Advisor now lives at the bottom of the Benchmarks page; keep old links working.
export default function AdvisorRedirect() {
  redirect("/compare/benchmarks");
}
