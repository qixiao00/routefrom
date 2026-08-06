import type { Metadata } from "next";
import "@fontsource-variable/ibm-plex-sans";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-sans-condensed/500.css";
import "@fontsource/ibm-plex-sans-condensed/600.css";
import "maplibre-gl/dist/maplibre-gl.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "RouteFrom — 足迹工作台",
  description: "探索、组织和理解你的时空足迹。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
