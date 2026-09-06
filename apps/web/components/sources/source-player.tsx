"use client";

import Hls, { Events } from "hls.js";
import Peaks, { type PeaksInstance } from "peaks.js";
import { useEffect, useRef, useState } from "react";

const HLS_CONFIG = {
  // hls.js guesses 500 kbps until measured, which would start every source
  // on the lowest rung; a review tool starts at the top rung and lets ABR
  // step down. Module-level so the engine is never reloaded on re-render.
  abrEwmaDefaultEstimate: 10_000_000,
  backBufferLength: 60,
  lowLatencyMode: false,
  maxBufferLength: 30,
  maxMaxBufferLength: 120,
} as const;

interface SourcePlayerProps {
  peaksUrl: string | null;
  playlistUrl: string;
  posterUrl: string | null;
}

/**
 * hls.js on a plain video element plus a peaks.js overview bound to it.
 * Client-only: peaks.js touches window at import. Peaks is initialised after
 * `loadedmetadata` so the duration is finite (bbc/peaks.js#574 leaves the
 * unplayed waveform blank when it is NaN at init).
 */
export function SourcePlayer({
  playlistUrl,
  peaksUrl,
  posterUrl,
}: SourcePlayerProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const overviewRef = useRef<HTMLDivElement | null>(null);
  const [waveformState, setWaveformState] = useState<
    "loading" | "ready" | "error" | "none"
  >(peaksUrl ? "loading" : "none");

  useEffect(() => {
    const video = videoRef.current;
    const overview = overviewRef.current;
    // biome-ignore lint/suspicious/noUnnecessaryConditions: a ref is null until the element mounts
    if (!video) {
      return;
    }
    let peaks: PeaksInstance | undefined;
    let hls: Hls | undefined;
    let cancelled = false;

    if (Hls.isSupported()) {
      hls = new Hls(HLS_CONFIG);
      hls.on(Events.ERROR, (_event, data) => {
        if (!data.fatal) {
          return;
        }
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls?.recoverMediaError();
        } else if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          hls?.startLoad();
        } else {
          hls?.destroy();
        }
      });
      hls.attachMedia(video);
      hls.loadSource(playlistUrl);
    } else {
      // Safari plays fMP4 HLS natively.
      video.src = playlistUrl;
    }

    const initPeaks = () => {
      if (
        cancelled ||
        !(peaksUrl && overview) ||
        !Number.isFinite(video.duration)
      ) {
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
    video.addEventListener("loadedmetadata", initPeaks, { once: true });

    return () => {
      cancelled = true;
      video.removeEventListener("loadedmetadata", initPeaks);
      peaks?.destroy();
      hls?.destroy();
    };
  }, [playlistUrl, peaksUrl]);

  return (
    <div className="flex flex-col gap-3" data-testid="source-player">
      {/* biome-ignore lint/a11y/useMediaCaption: captions are the S8 lane; the transcript arrives at S2 */}
      <video
        className="aspect-video w-full rounded-lg bg-black"
        controls
        crossOrigin="use-credentials"
        playsInline
        poster={posterUrl ?? undefined}
        preload="metadata"
        ref={videoRef}
      />
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
