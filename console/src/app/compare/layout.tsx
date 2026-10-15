import type { ReactNode } from "react";

import { CompareTabs } from "@/components/compare/shared";

export default function CompareLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <CompareTabs />
      {children}
    </>
  );
}
