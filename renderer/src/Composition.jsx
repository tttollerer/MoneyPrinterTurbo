import React, { useEffect, useState } from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  OffthreadVideo,
  Sequence,
  cancelRender,
  continueRender,
  delayRender,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

/** Same component and manifest drive the interactive Player and final render. */
export function VideoComposition({ manifest: m }) {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const [fontHandle] = useState(() => delayRender("Loading brand font"));
  const [fontReady, setFontReady] = useState(false);
  const brand = m.brand || {};
  const fontUrl = brand.font_asset_id
    ? m.assets[brand.font_asset_id]?.url
    : null;
  const fontFamily = fontUrl
    ? `SpotForge-${m.project_id}-${brand.version}`
    : brand.font_family || "Arial";
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (brand.font_asset_id && !fontUrl)
        throw new Error("Required brand font is missing");
      if (fontUrl) {
        const font = new FontFace(
          fontFamily,
          `url(${JSON.stringify(fontUrl)})`,
        );
        await font.load();
        document.fonts.add(font);
      }
      await document.fonts.ready;
      if (!cancelled) {
        setFontReady(true);
        continueRender(fontHandle);
      }
    };
    load().catch((error) => {
      if (!cancelled) cancelRender(error);
    });
    return () => {
      cancelled = true;
    };
  }, [fontUrl, fontFamily, fontHandle, brand.font_asset_id]);
  if (!fontReady) return null;
  const color = brand.colors || {};
  const margin = Math.round(
    Math.min(width, height) * (brand.safe_margin ?? 0.07),
  );
  const fontSize = Math.round(Math.min(width, height) * 0.05);
  const t = (frame / fps) * 1000;
  const cue = (m.captions || []).find((c) => c.start_ms <= t && t < c.end_ms);
  const logo = brand.logo_asset_id ? m.assets[brand.logo_asset_id] : null;
  const logoPos = brand.logo_position || "top-right";
  const bottomLogoSpace =
    logo && logoPos.startsWith("bottom") ? height * 0.15 : 0;
  const topLogoSpace = logo && logoPos.startsWith("top") ? height * 0.15 : 0;
  return (
    <AbsoluteFill
      style={{
        backgroundColor: color.background || "#10131a",
        color: color.text || "#fff",
        fontFamily,
      }}
    >
      {m.scenes.map((s) => {
        const a = m.assets[s.asset_id];
        if (!a) throw new Error(`Missing scene asset ${s.asset_id}`);
        return (
          <Sequence
            key={s.id}
            from={s.from_frame}
            durationInFrames={s.duration_frames}
          >
            {a.kind === "video" ? (
              <OffthreadVideo
                src={a.url}
                muted={!m.audio?.clip_audio}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
                onError={(e) => cancelRender(e)}
              />
            ) : (
              <Img
                src={a.url}
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
                onError={() =>
                  cancelRender(new Error(`Cannot load image: ${a.name}`))
                }
              />
            )}
            {s.onscreen_text && (
              <div
                style={{
                  position: "absolute",
                  top: margin + topLogoSpace,
                  left: margin,
                  right: margin,
                  fontSize: fontSize * 1.35,
                  fontWeight: 700,
                  textAlign: "center",
                  textShadow: "0 2px 7px #000",
                  padding: fontSize * 0.4,
                  background: "#0007",
                }}
              >
                {s.onscreen_text}
              </div>
            )}
          </Sequence>
        );
      })}
      {m.audio?.narration_asset_id && (
        <Audio
          src={m.assets[m.audio.narration_asset_id].url}
          volume={m.audio.narration_gain ?? 1}
        />
      )}
      {m.audio?.music_asset_id && (
        <Audio
          src={m.assets[m.audio.music_asset_id].url}
          volume={m.audio.music_gain ?? 0.15}
          loop
        />
      )}
      {logo && (
        <Img
          src={logo.url}
          style={{
            position: "absolute",
            width: width * 0.16,
            maxHeight: height * 0.13,
            objectFit: "contain",
            [logoPos.startsWith("top") ? "top" : "bottom"]: margin,
            [logoPos.endsWith("left") ? "left" : "right"]: margin,
          }}
        />
      )}
      {cue && (
        <div
          style={{
            position: "absolute",
            bottom:
              margin +
              bottomLogoSpace +
              (brand.required_text ? fontSize * 2 : 0),
            left: margin,
            right: margin,
            textAlign: "center",
            fontSize,
            fontWeight: 700,
            lineHeight: 1.3,
            textShadow: "0 2px 5px #000",
            background: "#000b",
            borderRadius: fontSize * 0.2,
            padding: fontSize * 0.4,
          }}
        >
          {brand.caption_style === "karaoke" && cue.words?.length
            ? cue.words.map((word, i) => (
                <span
                  key={i}
                  style={{
                    color:
                      t >= word.start_ms && t < word.end_ms
                        ? color.primary || "#ea765b"
                        : color.text || "#fff",
                  }}
                >
                  {word.text}{" "}
                </span>
              ))
            : cue.text}
        </div>
      )}
      {brand.required_text && (
        <div
          style={{
            position: "absolute",
            bottom: margin + bottomLogoSpace,
            left: margin,
            right: margin,
            textAlign: "center",
            fontSize: fontSize * 0.55,
            background: "#000b",
            padding: fontSize * 0.25,
          }}
        >
          {brand.required_text}
        </div>
      )}
    </AbsoluteFill>
  );
}
