import type { Metadata } from "next";
import { Geist } from "next/font/google";
import Link from "next/link";
import NavLinks from "@/components/NavLinks";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sentinel",
  description: "Value signal dashboard",
};

function LogoMark() {
  return (
    <svg
      width="28"
      height="28"
      viewBox="0 0 28 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ borderRadius: 6, flexShrink: 0, background: "#1D9E75" }}
    >
      <polyline
        points="4,22 10,14 16,18 24,8"
        stroke="white"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <polyline
        points="19,8 24,8 24,13"
        stroke="white"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${geistSans.variable}`}>
      <body>
        <header
          style={{
            height: 52,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 24px",
            borderBottom: "1px solid var(--color-border-tertiary)",
            position: "sticky",
            top: 0,
            background: "var(--color-background)",
            zIndex: 20,
          }}
        >
          <Link
            href="/"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              textDecoration: "none",
              color: "inherit",
            }}
          >
            <LogoMark />
            <span style={{ fontSize: 15, fontWeight: 500 }}>Sentinel</span>
          </Link>
          <NavLinks />
        </header>
        <main
          style={{
            maxWidth: 900,
            margin: "0 auto",
            padding: "24px 16px 48px",
          }}
        >
          {children}
        </main>
      </body>
    </html>
  );
}
