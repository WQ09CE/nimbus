import type { Metadata, Viewport } from "next";
import { Source_Sans_3, Fira_Code, Outfit } from "next/font/google";
import "./globals.css";

const sourceSans3 = Source_Sans_3({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const firaCode = Fira_Code({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
});

const outfit = Outfit({
  variable: "--font-display",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Nimbus",
  description: "AI Agent with DAG planning and tiered memory",
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${sourceSans3.variable} ${firaCode.variable} ${outfit.variable}`}>
      <body className="antialiased bg-[#0c1220] text-[#e2e8f0] font-sans selection:bg-sky-400/30">
        {children}
      </body>
    </html>
  );
}
