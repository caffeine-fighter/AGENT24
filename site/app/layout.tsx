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
    title: "NIGHTMARE LAB — AI 에이전트 안전 실험실",
    description:
      "행동형 AI 에이전트를 실제로 배포하기 전에 가상 환경에서 실패를 재현하고 필요한 보호책을 검증합니다.",
    alternates: { canonical: metadataBase },
    openGraph: {
      title: "NIGHTMARE LAB — AI 에이전트 안전 실험실",
      description: "에이전트를 실제로 배포하기 전에 가상 환경에서 먼저 시험해 보세요.",
      type: "website",
      url: metadataBase,
    },
    twitter: {
      card: "summary",
      title: "NIGHTMARE LAB — AI 에이전트 안전 실험실",
      description: "AI 에이전트를 실전에 투입하기 전에 가상 환경에서 안전하게 시험합니다.",
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
