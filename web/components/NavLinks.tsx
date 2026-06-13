"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function NavLinks() {
  const pathname = usePathname();
  const isPicksActive = pathname === "/" || pathname.startsWith("/date");
  const isPnlActive = pathname.startsWith("/pnl");

  const link = (href: string, label: string, active: boolean) => (
    <Link
      href={href}
      style={{
        fontSize: 14,
        fontWeight: active ? 500 : 400,
        color: active ? "var(--color-text-primary)" : "var(--color-text-secondary)",
        textDecoration: "none",
        paddingBottom: 2,
        borderBottom: active ? "2px solid #1D9E75" : "2px solid transparent",
      }}
    >
      {label}
    </Link>
  );

  return (
    <nav style={{ display: "flex", gap: 24 }}>
      {link("/", "Picks", isPicksActive)}
      {link("/pnl", "P&L", isPnlActive)}
    </nav>
  );
}
