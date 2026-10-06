import { useMemo, useState } from "react";
import {
  RAMP_CNN_SCENES,
  padRampCnnFrame,
  rampCnnFrameUrl,
  type RampCnnFrameKind,
  type RampCnnScene,
} from "./rampCnnFrameConfig";

const COLUMNS: Array<{ kind: RampCnnFrameKind; title: string; subtitle: string }> = [
  { kind: "camera/real", title: "Camera", subtitle: "Real" },
  { kind: "camera/sim", title: "Camera", subtitle: "AuRaSim" },
  { kind: "radar/real", title: "Radar", subtitle: "Real" },
  { kind: "radar/sim", title: "Radar", subtitle: "AuRaSim" },
];

function ScrubImage({ src, alt }: { src: string; alt: string }) {
  const [missing, setMissing] = useState(false);

  return (
    <div className="relative aspect-[4/3] overflow-hidden rounded-lg bg-zinc-100 dark:bg-zinc-950">
      <img
        src={src}
        alt={alt}
        draggable={false}
        onLoad={() => setMissing(false)}
        onError={() => setMissing(true)}
        className="h-full w-full select-none object-contain"
      />
      {missing && (
        <div className="absolute inset-0 grid place-items-center bg-zinc-100/95 px-3 text-center text-xs text-zinc-500 dark:bg-zinc-950/95 dark:text-zinc-400">
          <span>
            Frame not uploaded
            <br />
            <code className="break-all text-[10px]">{src}</code>
          </span>
        </div>
      )}
    </div>
  );
}

function SceneRow({ scene }: { scene: RampCnnScene }) {
  const [frame, setFrame] = useState(1);

  const sources = useMemo(
    () =>
      COLUMNS.map((column) => ({
        ...column,
        src: rampCnnFrameUrl(scene.id, column.kind, frame),
      })),
    [scene.id, frame],
  );

  return (
    <section className="overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
      <div className="flex items-end justify-between gap-4 border-b border-zinc-200 px-4 py-3 dark:border-zinc-700">
        <div>
          <div className="font-semibold text-zinc-900 dark:text-zinc-100">{scene.label}</div>
          <div className="text-xs text-zinc-500 dark:text-zinc-400">{scene.subtitle}</div>
        </div>
        <div className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
          Frame {padRampCnnFrame(frame)} / {scene.frames}
        </div>
      </div>

      <div className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-4">
        {sources.map((item) => (
          <div key={item.kind} className="min-w-0">
            <div className="mb-1.5 flex items-baseline justify-between gap-2 px-0.5">
              <span className="text-xs font-semibold text-zinc-800 dark:text-zinc-100">{item.title}</span>
              <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                {item.subtitle}
              </span>
            </div>
            <ScrubImage src={item.src} alt={`${scene.label} ${item.title} ${item.subtitle}`} />
          </div>
        ))}
      </div>

      <div className="grid grid-cols-[1fr_auto] items-center gap-3 border-t border-zinc-200 px-4 py-3 dark:border-zinc-700">
        <input
          aria-label={`${scene.label} synchronized frame`}
          type="range"
          min={1}
          max={scene.frames}
          value={frame}
          onChange={(event) => setFrame(Number(event.target.value))}
          className="w-full accent-cyan-600"
        />
        <span className="min-w-[5.5rem] text-right font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
          {padRampCnnFrame(frame)}
        </span>
      </div>
    </section>
  );
}

export default function RampCnnSim2RealFrames() {
  return (
    <div className="not-prose my-6 space-y-4">
      <div className="hidden grid-cols-4 gap-3 px-3 xl:grid">
        {COLUMNS.map((column) => (
          <div key={column.kind} className="text-center">
            <div className="text-xs font-semibold text-zinc-800 dark:text-zinc-100">{column.title}</div>
            <div className="text-[10px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">{column.subtitle}</div>
          </div>
        ))}
      </div>
      {RAMP_CNN_SCENES.map((scene) => (
        <SceneRow key={scene.id} scene={scene} />
      ))}
    </div>
  );
}
