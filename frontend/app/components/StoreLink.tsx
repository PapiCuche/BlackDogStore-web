import Link from "next/link";

/**
 * A link whose destination the STORE wrote — a campaign's button, a policy, a
 * map. Inside the site it is a client-side navigation; anywhere else it opens
 * apart and tells the other site nothing about where the visitor came from.
 */
export function StoreLink({ href, children, className }: {
  href: string; children: React.ReactNode; className?: string;
}) {
  if (href.startsWith("/") && !href.startsWith("//")) {
    return <Link href={href} className={className}>{children}</Link>;
  }
  return (
    <a
      href={href}
      className={className}
      {...(/^https?:/.test(href) ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}
