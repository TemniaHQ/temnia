import type { Metadata } from "next";
import "./globals.css";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";

const geist = Geist({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  description: "The cutting room for the whole episode.",
  title: "Temnia",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      className={cn("h-full antialiased", "font-sans", geist.variable)}
      lang="en"
    >
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
