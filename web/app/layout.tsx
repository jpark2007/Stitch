import type { Metadata, Viewport } from "next";
import { Caveat, Fraunces, Nunito_Sans } from "next/font/google";
import "./globals.css";

// Storybook serif for headings (soft, slightly wonky at display sizes).
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  axes: ["SOFT", "WONK", "opsz"],
  display: "swap",
});

// Handwriting for captions and notes.
const caveat = Caveat({
  variable: "--font-caveat",
  subsets: ["latin"],
  display: "swap",
});

// Quiet, rounded sans for UI text.
const nunito = Nunito_Sans({
  variable: "--font-nunito",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Stitch",
  description: "A pop-up storybook of your family, made from everyone's memories.",
};

export const viewport: Viewport = {
  themeColor: "#f5eee1",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${fraunces.variable} ${caveat.variable} ${nunito.variable} antialiased`}
    >
      <body>{children}</body>
    </html>
  );
}
