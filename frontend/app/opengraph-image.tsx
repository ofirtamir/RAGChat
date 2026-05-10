import { ImageResponse } from "next/og"

export const runtime = "edge"
export const alt = "שמאות AI – מערכת חיפוש חכמה במסמכים"
export const size = { width: 1200, height: 630 }
export const contentType = "image/png"

async function loadFont(): Promise<ArrayBuffer | null> {
  try {
    // Old user-agent makes Google Fonts return a single woff file
    // (with all Unicode subsets merged) instead of separate per-range woff2 files.
    const css = await fetch(
      "https://fonts.googleapis.com/css?family=Rubik:700&subset=hebrew,latin",
      {
        headers: {
          "User-Agent":
            "Mozilla/4.0 (compatible; MSIE 5.0; Windows NT 5.1; Trident/4.0)",
        },
      }
    ).then((r) => r.text())

    const url = css.match(/url\(([^)]+)\)/)?.[1]
    if (!url) return null
    return fetch(url).then((r) => r.arrayBuffer())
  } catch {
    return null
  }
}

export default async function Image() {
  const fontData = await loadFont()
  const fontFamily = fontData ? "Rubik" : "sans-serif"

  return new ImageResponse(
    (
      <div
        style={{
          background: "linear-gradient(140deg, #0369a1 0%, #0ea5e9 55%, #06b6d4 100%)",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          padding: "60px",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Soft radial highlight */}
        <div
          style={{
            position: "absolute",
            top: "-60px",
            right: "-60px",
            width: "500px",
            height: "500px",
            background:
              "radial-gradient(ellipse, rgba(255,255,255,0.18) 0%, transparent 70%)",
            display: "flex",
          }}
        />
        {/* Bottom-left shadow */}
        <div
          style={{
            position: "absolute",
            bottom: "-80px",
            left: "-80px",
            width: "420px",
            height: "420px",
            background:
              "radial-gradient(ellipse, rgba(3,105,161,0.5) 0%, transparent 70%)",
            display: "flex",
          }}
        />

        {/* Document icon */}
        <div style={{ fontSize: "100px", marginBottom: "24px", display: "flex" }}>
          📋
        </div>

        {/* Main title */}
        <div
          style={{
            fontSize: "88px",
            fontWeight: "700",
            color: "white",
            fontFamily,
            textAlign: "center",
            lineHeight: 1.1,
            marginBottom: "22px",
            display: "flex",
          }}
        >
          שמאות AI
        </div>

        {/* Subtitle */}
        <div
          style={{
            fontSize: "34px",
            color: "rgba(255,255,255,0.88)",
            fontFamily,
            textAlign: "center",
            display: "flex",
          }}
        >
          מערכת חיפוש חכמה במסמכים
        </div>

        {/* Divider line */}
        <div
          style={{
            width: "120px",
            height: "3px",
            background: "rgba(255,255,255,0.4)",
            borderRadius: "2px",
            marginTop: "36px",
            display: "flex",
          }}
        />

        {/* Domain */}
        <div
          style={{
            marginTop: "20px",
            fontSize: "22px",
            color: "rgba(255,255,255,0.55)",
            fontFamily: "monospace",
            display: "flex",
            letterSpacing: "0.03em",
          }}
        >
          rag-chat-smoky-alpha.vercel.app
        </div>
      </div>
    ),
    {
      ...size,
      fonts: fontData
        ? [{ name: "Rubik", data: fontData, weight: 700, style: "normal" }]
        : [],
    }
  )
}
