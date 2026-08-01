import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const metadataBase = new URL(`${protocol}://${host}`);
  const socialImage = new URL("/og.png", metadataBase);

  return {
    metadataBase,
    title: "NIGHTMARE LAB | AI 에이전트 안전 테스트",
    description:
      "GitHub 저장소와 맡길 일을 입력하면 AI 에이전트의 실패를 가상 환경에서 재현하고, 안전장치 적용 뒤 같은 조건으로 다시 확인해요.",
    alternates: { canonical: metadataBase },
    openGraph: {
      title: "NIGHTMARE LAB | AI 에이전트 안전 테스트",
      description: "AI 에이전트의 실패를 가상 환경에서 재현하고, 안전장치를 적용한 뒤 같은 조건으로 다시 확인해요.",
      type: "website",
      url: metadataBase,
      images: [{
        url: socialImage,
        width: 1731,
        height: 909,
        alt: "NIGHTMARE LAB — 배포하기 전에 먼저 실패시켜 보세요.",
      }],
    },
    twitter: {
      card: "summary_large_image",
      title: "NIGHTMARE LAB | AI 에이전트 안전 테스트",
      description: "AI 에이전트의 실패를 배포 전에 재현하고 안전장치까지 확인해요.",
      images: [socialImage],
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
