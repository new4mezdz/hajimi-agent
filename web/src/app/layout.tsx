import type { Metadata } from "next";
import { agentBrand } from "./brand";
import "./globals.css";

export const metadata: Metadata = {
  title: `${agentBrand.name} — AI Workspace`,
  description: agentBrand.description,
  icons: {
    icon: agentBrand.iconSrc,
    apple: agentBrand.iconSrc,
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
