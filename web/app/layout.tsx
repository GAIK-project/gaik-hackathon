import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Sora } from "next/font/google";

import { Dock } from "@/components/shell/dock";
import { ShellProvider } from "@/components/shell/shell-provider";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

// Sora for everything, JetBrains Mono for component and tool names (section 14).
// "latin-ext" is not needed for Finnish: ä, ö and å are in "latin".
const sora = Sora({
  variable: "--font-sora",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "GAIK mini-hackathon",
    template: "%s | GAIK mini-hackathon",
  },
  description: "Neljä demoa, jotka on rakennettu gaik-kirjaston komponenteista.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Lets env(safe-area-inset-bottom) keep the dock above the home indicator.
  viewportFit: "cover",
  themeColor: "#070c18",
  colorScheme: "dark",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="fi" className={`dark ${sora.variable} ${jetbrainsMono.variable} antialiased`}>
      <body className="min-h-dvh">
        <ShellProvider>
          {children}
          <Dock />
        </ShellProvider>
        <Toaster />
      </body>
    </html>
  );
}
