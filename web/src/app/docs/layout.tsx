import { DocsNav } from "@/components/DocsNav";

/**
 * Two columns: navigation that stays put, and a reading column that does not
 * grow past a comfortable measure however wide the window gets.
 */
export default function DocsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto flex max-w-7xl gap-8 px-4 py-8 sm:px-6">
      <aside className="hidden w-56 shrink-0 lg:block">
        <div className="sticky top-20">
          <DocsNav />
        </div>
      </aside>
      <main className="min-w-0 flex-1 pb-12">{children}</main>
    </div>
  );
}
