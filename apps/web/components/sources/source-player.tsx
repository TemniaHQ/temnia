"use client";

import "@videojs/react/video/skin.css";
import { HlsJsVideo } from "@videojs/react/media/hlsjs-video";
import { VideoSkin } from "@videojs/react/video";
import Peaks, { type PeaksInstance } from "peaks.js";
import { useEffect, useRef, useState } from "react";

/**
 * hls.js guesses 500 kbps until measured, which would start every source on
 * the lowest rung; a review tool starts at the top rung and lets ABR step
 * down. Module-level so the engine is never recreated on re-render (the
 * source object's engine options are read when the engine is constructed).
 */
const HLS_JS_CONFIG = {
  abrEwmaDefaultEstimate: 10_000_000,
  backBufferLength: 60,
  lowLatencyMode: false,
  maxBufferLength: 30,
  maxMaxBufferLength: 120,
} as const;

interface SourcePlayerProps {
  peaksUrl: string | null;
  playlistUrl: string;
}

/**
 * Video.js v10's React skin (Rajesh's pick, 2026-09-06) over its hls.js media
 * element, with a peaks.js overview bound to the underlying video element.
 * Client-only: peaks.js touches window at import. Peaks is initialised after
 * `loadedmetadata`, once the duration is finite (bbc/peaks.js#574), which is
 * also after the engine's mount-time MediaSource attach has settled.
 *
 * The `VideoPlayer` provider is not here but around both panes, in
 * `SourceWorkspace`: it renders no element of its own and it is what lets the
 * transcript tab read and drive this player through `usePlayer` rather than
 * through a ref lifted out of this file (S2 plan §5).
 */
export function SourcePlayer({ playlistUrl, peaksUrl }: SourcePlayerProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overviewRef = useRef<HTMLDivElement | null>(null);
  const [waveformState, setWaveformState] = useState<
    "loading" | "ready" | "error" | "none"
  >(peaksUrl ? "loading" : "none");

  useEffect(() => {
    const video = videoRef.current;
    const overview = overviewRef.current;
    // biome-ignore lint/suspicious/noUnnecessaryConditions: a ref is null until the element mounts
    if (!(video && overview && peaksUrl)) {
      return;
    }
    let peaks: PeaksInstance | undefined;
    let cancelled = false;

    const initPeaks = () => {
      if (cancelled || !Number.isFinite(video.duration)) {
        return;
      }
      Peaks.init(
        {
          dataUri: { json: peaksUrl },
          keyboard: false,
          mediaElement: video,
          overview: {
            container: overview,
            playedWaveformColor: "oklch(0.55 0.2 260)",
            waveformColor: "oklch(0.75 0.03 260)",
          },
        },
        (error, instance) => {
          if (cancelled) {
            instance?.destroy();
            return;
          }
          if (error || !instance) {
            setWaveformState("error");
            return;
          }
          peaks = instance;
          setWaveformState("ready");
        }
      );
    };
    if (Number.isFinite(video.duration) && video.duration > 0) {
      initPeaks();
    } else {
      video.addEventListener("loadedmetadata", initPeaks, { once: true });
    }
    return () => {
      cancelled = true;
      video.removeEventListener("loadedmetadata", initPeaks);
      peaks?.destroy();
    };
  }, [peaksUrl]);

  return (
    <div className="flex flex-col gap-3" data-testid="source-player">
      <VideoSkin className="aspect-video w-full overflow-hidden rounded-lg">
        <HlsJsVideo
          crossOrigin="use-credentials"
          playsInline
          preload="metadata"
          ref={videoRef}
          source={{
            engine: { hlsJs: HLS_JS_CONFIG },
            src: playlistUrl,
            type: "application/vnd.apple.mpegurl",
          }}
        />
      </VideoSkin>
      {peaksUrl ? (
        <div
          className="h-20 w-full rounded-md border bg-muted/30"
          data-testid="waveform-overview"
          data-waveform-state={waveformState}
          ref={overviewRef}
        />
      ) : null}
    </div>
  );
}
