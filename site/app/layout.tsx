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
    title: "NIGHTMARE LAB | AI 에이전트 안전성 테스트",
    description:
      "도구를 직접 쓰는 AI 에이전트를 배포하기 전에, 가상 환경에서 실패를 재현하고 안전장치를 확인해 보세요.",
    alternates: { canonical: metadataBase },
    openGraph: {
      title: "NIGHTMARE LAB | AI 에이전트 안전성 테스트",
      description: "AI 에이전트가 실수하는 순간을 가상 환경에서 재현하고 안전장치까지 확인해 보세요.",
      type: "website",
      url: metadataBase,
    },
    twitter: {
      card: "summary",
      title: "NIGHTMARE LAB | AI 에이전트 안전성 테스트",
      description: "AI 에이전트를 배포하기 전에 실패와 안전장치를 가상 환경에서 확인해 보세요.",
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
