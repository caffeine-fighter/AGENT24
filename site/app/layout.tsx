import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);

  return {
    metadataBase,
    title: "NIGHTMARE LAB — Agent Crash Test",
    description:
      "행동형 AI 에이전트를 합성 세계에서 먼저 충돌시키고, 원인을 해부해 최소 안전 패치를 재검증합니다.",
    alternates: { canonical: metadataBase },
    openGraph: {
      title: "NIGHTMARE LAB — Agent Crash Test",
      description: "Break it before reality does: CLONE → CRASH → AUTOPSY → VACCINE → REPLAY.",
      type: "website",
      url: metadataBase,
    },
    twitter: {
      card: "summary",
      title: "NIGHTMARE LAB — Agent Crash Test",
      description: "AI Agent를 현실에 투입하기 전에 합성 세계에서 충돌 시험합니다.",
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
